/* 电容辨识调度：ADC中断仅采集原始样本，主循环分步预处理和推理。 单次窗口1024点，三个通道依次为物理Vc1、ic、io；不在中断内运行CNN。 */ #include "cap_monitor.h"
#include "cap_model.h"
#include "cap_port.h"
#include "cap_sci.h"
#include "main.h"
#include <math.h>

#if defined(__TI_COMPILER_VERSION__)
#pragma DATA_SECTION(samples, ".cap_capture")
#endif
                                                                           
/* 采样缓存只由ADC入口写入；填满后发布WAVELET状态，前台读取期间不再写入。 */
static volatile uint16_t samples[1024][3];
/* CCS控制：Enable允许辨识，Request触发单次采样，State记录阶段，Valid表示结果可用。 */ volatile uint16_t g_capEnable=1U, g_capRequest=0U, g_capState=CAP_IDLE;
volatile uint16_t g_capValid=0U, g_capFault=0U, g_capWarning=3U;
volatile uint16_t g_capSampleCount=0U;
volatile uint32_t g_capSequence=0UL, g_capErrorCount=0UL;
volatile uint32_t g_capAdcOverflowCount=0UL;
/* 四路输出按物理P1/P2/P3/P4排列；C单位uF，ESR同时提供Ohm与mOhm。 */ volatile float g_capC_uF[4]={0}, g_capESR_Ohm[4]={0}, g_capESR_mOhm[4]={0};
volatile float g_capSampleRateHz=0, g_capMaxSampleGapUs=0;
volatile float g_capPreprocessUs=0, g_capInferUs=0, g_capTaskMaxUs=0;
volatile float g_capBackgroundMaxUs=0;
volatile float g_capLatencyUs=0, g_capAdcIsrMaxUs=0;
volatile float g_capInputMin[3], g_capInputMax[3], g_capInputMean[3];
/* 本次窗口使用的标定快照，避免一个窗口混用不同的增益/偏置。 */ volatile float g_capOffsetUsed[3], g_capGainUsed[3];
static volatile uint32_t first_tick,last_tick,max_gap,expected_ticks;
static volatile uint16_t pending_error;
static uint16_t process_index;
static uint32_t clock_hz;
static uint32_t arm_tick;
static float output[8], input_sum[3], us_per_tick;
/* 统一错误出口：累积故障位、撤销结果有效性，延迟到主循环发送状态。 */
static void fail(uint16_t reason)
{
    g_capFault|=reason; g_capValid=0U;
    g_capState=CAP_ERROR; pending_error=1U;
}
/* 复位调度与模型内部状态；默认启用，但需要Request才能启动单次采样。 */
void CapMonitor_Init(void)
{
    g_capEnable=1U; g_capRequest=0U; g_capState=CAP_IDLE;
    g_capValid=0U; g_capFault=0U; g_capWarning=3U;
    pending_error=0U; Cap_Reset();
}
/* 实时采样钩子：tick为Timer0向下计数值，overflow为ADC溢出状态。 仅CAP_CAPTURE接收数据；拒绝码值触轨或采样间隔超出预期0.5~1.5倍的窗口。 */
void CapMonitor_OnAdc(uint16_t v, uint16_t ic, uint16_t io,
                      uint32_t tick, uint16_t overflow)
{
    uint16_t n;
    uint32_t gap;
    if (overflow) g_capAdcOverflowCount++; /* 只要传入溢出事件就累计计数，不要求当前正在采样。 */
    if (g_capState!=CAP_CAPTURE) return; /* 仅采样状态接受新点；其他阶段不改冻结缓存。 */
    if (!g_capEnable) { fail(CAP_ERR_CANCEL); return; } /* 采样中被关闭时取消当前窗口，不继续接收数据。 */
    if (overflow) { fail(CAP_ERR_ADC_OVERFLOW); return; } /* 采样窗口内发生溢出则本次辨识无效。 */
    if (v==0U || v>=4095U || ic==0U || ic>=4095U || io==0U || io>=4095U) { /* 任一通道触及0或满量程边界就拒绝整个窗口，防止用饱和数据推理。 */
        fail(CAP_ERR_ADC_RAIL); return;
    }
    n=g_capSampleCount;
    if (n==0U) first_tick=tick; /* 第一点只记录起始时间；后续点才检查相邻间隔。 */
    else {
        gap=(uint32_t)(last_tick-tick);
        if (gap>max_gap) max_gap=gap; /* 保留窗口内最大的相邻采样间隔，供时序诊断。 */
                                                                               
        if (gap<expected_ticks/2UL || gap>expected_ticks+expected_ticks/2UL) { /* 间隔低于预期一半或高于1.5倍时判为时序异常。 */
            g_capMaxSampleGapUs=(float)gap*us_per_tick;
            fail(CAP_ERR_TIMING); return;
        }
    }
    last_tick=tick;
    samples[n][0]=v; samples[n][1]=ic; samples[n][2]=io;
    g_capSampleCount=n+1U;
    if (n==1023U) g_capState=CAP_WAVELET; /* 第1024点写入后冻结窗口，交给前台处理。 */
}
/* 消费请求并检查启动条件，目标采样率100kHz。标定快照和模型复位完成后， 最后写CAP_CAPTURE，使中断不会读取尚未初始化的采样状态。 */
static void arm(uint16_t ready)
{
    int c;
    g_capRequest=0U; g_capValid=0U; g_capFault=0U; g_capWarning=3U;
    g_capSampleCount=0U; max_gap=0UL; process_index=0U;
    g_capSampleRateHz=0; g_capMaxSampleGapUs=0;
    g_capPreprocessUs=0; g_capInferUs=0; g_capTaskMaxUs=0;
    g_capLatencyUs=0;
    if (!ready) { fail(CAP_ERR_NOT_READY); return; } /* 启动条件未满足时结束本次请求，不进入采样。 */
    clock_hz=CapPort_Clock();
    if (clock_hz==0UL) { fail(CAP_ERR_CONFIG); return; } /* 无有效计数频率时不能计算采样间隔和时间。 */
    expected_ticks=clock_hz/100000UL;
    us_per_tick=1000000.0f/(float)clock_hz;
    g_capOffsetUsed[0]=ADC_Sample_Offset.Vs1;
    g_capOffsetUsed[1]=ADC_Sample_Offset.ic;
    g_capOffsetUsed[2]=ADC_Sample_Offset.io;
    g_capGainUsed[0]=ADC_Sample_Gain.Vs1;
    g_capGainUsed[1]=ADC_Sample_Gain.ic;
    g_capGainUsed[2]=ADC_Sample_Gain.io;
    for (c=0; c<3; c++) {
        if (!isfinite(g_capOffsetUsed[c]) || !isfinite(g_capGainUsed[c]) ||
            g_capGainUsed[c]==0.0f) { fail(CAP_ERR_CONFIG); return; }
        input_sum[c]=0.0f; g_capInputMin[c]=3.402823466e38f;
        g_capInputMax[c]=-3.402823466e38f; g_capInputMean[c]=0;
    }
    Cap_Reset();
    arm_tick=CapPort_Timer();
                                                                            
    g_capState=CAP_CAPTURE;
}
/* 前台协作调度，每次只完成有限工作：采集阶段检查100ms超时； 小波阶段每次最多送入4个样本；成像和CNN由Cap_FinishStep分步推进。 */
void CapMonitor_Task(uint16_t ready)
{
    uint32_t start, elapsed;
    uint16_t old_state, batch, c;
    int rc;
    float value[3], dt;
    if (pending_error) { /* 错误状态只在前台发布，避免在ADC中断中组装串口帧。 */
        pending_error=0U; g_capErrorCount++; CapSCI_Publish();
    }
    if (!g_capEnable) { /* 总使能关闭时不推进辨识；如仍处于活动阶段则取消。 */
        if (g_capState>=CAP_CAPTURE && g_capState<=CAP_CNN) fail(CAP_ERR_CANCEL);
        return;
    }
    if (g_capState==CAP_IDLE || g_capState==CAP_DONE || g_capState==CAP_ERROR) { /* 只有空闲、完成或错误状态才消费新的采样请求。 */
        if (g_capRequest) arm(ready); /* 请求为1时尝试启动，启动函数内部负责清除请求并验证配置。 */
        return;
    }
    if (g_capState==CAP_CAPTURE) { /* 采样期间前台只检查超时，不读取尚未填满的缓存。 */
        if ((uint32_t)(arm_tick-CapPort_Timer())>clock_hz/10UL) fail(CAP_ERR_TIMING);
        return;
    }
    old_state=g_capState; start=CapPort_Timer();
    if (old_state==CAP_WAVELET) { /* 采样结束后分批换算物理量并推进小波，每次最多处理4点。 */
        /* 由1023个相邻间隔估算实际采样率，接受98~102kHz的有效窗口。 */ if (process_index==0U) { /* 窗口首次进入预处理时校验实际采样率，后续批次不重复计算。 */
            elapsed=(uint32_t)(first_tick-last_tick);
            if (!elapsed) { fail(CAP_ERR_TIMING); return; }
            g_capSampleRateHz=(float)clock_hz*1023.0f/(float)elapsed;
            g_capMaxSampleGapUs=(float)max_gap*us_per_tick;
            if (g_capSampleRateHz<98000.0f || g_capSampleRateHz>102000.0f) { /* 仅接受98~102kHz窗口，超出范围则不送入模型。 */
                fail(CAP_ERR_TIMING); return;
            }
        }
        for (batch=0; batch<4U && process_index<1024U; batch++) {
            for (c=0; c<3; c++) {
                value[c]=((float)samples[process_index][c]-g_capOffsetUsed[c])*g_capGainUsed[c];
                input_sum[c]+=value[c];
                if (value[c]<g_capInputMin[c]) g_capInputMin[c]=value[c];
                if (value[c]>g_capInputMax[c]) g_capInputMax[c]=value[c];
            }
            rc=Cap_Push(value[0],value[1],value[2]);
            if (rc<0) { fail(CAP_ERR_MODEL); return; }
            process_index++;
        }
        if (process_index==1024U) { /* 全部样本完成小波累积后，才开始分步成像及CNN推理。 */
            for (c=0; c<3; c++) g_capInputMean[c]=input_sum[c]/1024.0f;
            if (Cap_BeginFinish()!=0) { fail(CAP_ERR_MODEL); return; }
            g_capState=CAP_IMAGE;
        }
    } else {
        rc=Cap_FinishStep(output);
        if (rc<0) { fail(CAP_ERR_MODEL); return; }
        if (Cap_GetPhase()>=2U && g_capState==CAP_IMAGE) g_capState=CAP_CNN;
        /* 先验证全部8个输出的有限性及物理范围，再一次发布四路结果。 Warning的0x4位提示原预测超出训练标签区间；CAP_DONE与Valid表示计算已完成。 */ if (rc==1) { /* 分步接口返回1表示输出齐全；先验证全部结果再置有效位。 */
            for (c=0; c<4; c++) {
                if (!isfinite(output[c]) || !isfinite(output[c+4]) ||
                    output[c]<=0 || output[c]>10000 || output[c+4]<=0 || output[c+4]>10) {
                    fail(CAP_ERR_MODEL); return;
                }
            }
            for (c=0; c<4; c++) {
                g_capC_uF[c]=output[c]; g_capESR_Ohm[c]=output[c+4];
                g_capESR_mOhm[c]=1000.0f*output[c+4];
                if (output[c]<505.199f || output[c]>602.201f ||
                    output[c+4]<0.239999f || output[c+4]>0.320001f) g_capWarning|=4U;
            }
            g_capLatencyUs=(float)(uint32_t)(first_tick-CapPort_Timer())*us_per_tick;
            g_capSequence++; g_capState=CAP_DONE; g_capValid=1U;
        }
    }
    dt=(float)(uint32_t)(start-CapPort_Timer())*us_per_tick;
    if (old_state==CAP_WAVELET || old_state==CAP_IMAGE) g_capPreprocessUs+=dt;
    else g_capInferUs+=dt;
    if (dt>g_capTaskMaxUs) g_capTaskMaxUs=dt;
    if (g_capState==CAP_DONE) CapSCI_Publish(); /* 只有成功完成的当前事件在此处发布；错误由pending_error路径发布。 */
}
/* 主循环入口：依次推进辨识和状态发送，记录整次后台调用的最大耗时。 */
void CapBackground_Task(uint16_t ready)
{
    uint32_t start=CapPort_Timer(), clock=CapPort_Clock();
    float us;
    CapMonitor_Task(ready);
    CapSCI_Task();
    if (clock) {
        us=(float)(uint32_t)(start-CapPort_Timer())*(1000000.0f/(float)clock);
        if (us>g_capBackgroundMaxUs) g_capBackgroundMaxUs=us;
    }
}
