    /* 主程序：设备初始化、ADC实时控制及前台任务调度。
     * ADC中断读取反馈、更新滤波与双环PI；主循环处理推理请求和串口。 */
    #include "driverlib.h"
    #include "device.h"
    #include "board.h"
    #include "c2000ware_libraries.h"
    #include "main.h"
    #include "nn_cpu_test.h"
    #include "portal_sci.h"
    #include "cap_monitor.h"
    #include "cap_sci.h"                
      
    /* 电压环与电流环状态；每次ADC中断先更新电压环，再更新电流环。 */
    struct CON_REG VoltCon_Reg;
    struct CON_REG CurrCon_Reg;

    __uint16_t i= 0;

    /* 采样数据链：Raw为ADC码值，Offset为零点码值，Gain为物理量/码值；
     * RealValue为换算后的反馈，Filtered为一阶递推滤波反馈。 */
    struct ADC_Sample_Reg ADC_Sample_Offset;
    struct ADC_Sample_Reg ADC_Sample_Gain;
    struct ADC_Sample_Reg ADC_Sample_RealValue;
    struct ADC_Sample_Reg ADC_Sample_Raw;
    struct ADC_Sample_Reg ADC_Sample_Filtered;

    /* 快、慢通道分别使用下列更新系数；实际滤波响应取决于中断周期。 */
    #define LPF_ALPHA_10kHz  0.628f                       
    #define LPF_ALPHA_10Hz   0.0000628f                       

    /* PWM比较值换算使用的周期计数，需与实际时基配置配套。 */
    #define EPWM_TIMER_TBPRD 750

    /* 边沿诊断：当拍电流、当拍差分及前一拍电流，单位均为A。 */
    volatile float AnnRiseTriggerIo = 0.0f;                             
    volatile float AnnRiseTriggerStep = 0.0f;                                          
    volatile float AnnRisePreviousIo = 0.0f;                         
    volatile uint32_t AnnRiseDetectCount = 0UL;
    volatile uint32_t AnnRiseWriteCount  = 0UL;

    /* 人工设置的高电流参考，单位A；同时用作边沿高平台判断和模型峰值输入。 */
    volatile float Io_Peak = 18.0f;                                  
    /* 待处理请求位：bit0为上升沿，bit1为下降沿；中断置位，主循环消费。 */
    volatile uint16_t AnnUpdateReq = 0U;                                                           
    /* 模型的人工时间输入，单位s；0.00005s对应50us。 */
    volatile float Rise_time = 0.00005f;                         

    volatile float AnnDutyRise = 0.0f;                                

    volatile float AnnRiseDutyCorrection = -0.3f;                                                 
    volatile float AnnFallDutyCorrection = 0.0f;                                                                   

    #define ANN_RISE_HOLD_CYCLES 2U                                               
    #define ANN_FALL_HOLD_CYCLES 3U                                               

    volatile float AnnDutyFall = 0.0f;                                
    /* 滤波Vin首次达到265V时置1并锁存；此状态也作为后台采样就绪条件。 */
    volatile uint16_t AnnStartEnable = 0U;                                                
    /* 两方向缓存有效标志；与AnnPwmActive所表示的本拍实际PWM选择不同。 */
    volatile uint16_t AnnInferenceReady = 0U;                                                  
    volatile uint16_t AnnPwmActive = 0U;                                              
    volatile float AnnPiOutputBeforeReplace = 0.0f;                                                                              
    volatile float AnnPiOutputAtEdge[2] = {0.0f, 0.0f};                                                               
    volatile float count = 0.0f;
                                                                                                    
    /* 按方向保存最近一次接管窗口内的电流环PI输出；计数给出数组有效长度。 */
    #define ANN_PI_TRACE_LEN 5U
    volatile float AnnRisePiTrace[ANN_PI_TRACE_LEN] = {0.0f};
    volatile float AnnFallPiTrace[ANN_PI_TRACE_LEN] = {0.0f};
    volatile uint16_t AnnRisePiTraceCount = 0U;
    volatile uint16_t AnnFallPiTraceCount = 0U;

    #define ANN_RISE_REQ  0x0001U
    #define ANN_FALL_REQ  0x0002U
    #define IO_LOW_A      1.0f
    volatile uint16_t AnnDirection = 0U;                                                  
    volatile uint16_t AnnDutyRiseValid = 0U;
    volatile uint16_t AnnDutyFallValid = 0U;
                                                                                                      
    /* 边沿输入快照：下标0为上升沿，1为下降沿；主循环按方向独立读取。 */
    volatile float AnnCapturedPeak[2] = {0.0f, 0.0f};
    volatile float AnnCapturedTime[2] = {0.0f, 0.0f};
    volatile float AnnCapturedVc1[2] = {0.0f, 0.0f};
    volatile float AnnCapturedVs1[2] = {0.0f, 0.0f};

    /* 边沿判据：相邻采样电流变化量超过2A；平台边界为
     * IO_LOW_A+IO_NEAR_ZERO_A和Io_Peak-IO_NEAR_PEAK_A。 */
    #define IO_CHANGE_THRESHOLD   2.0f

                              
    #define IO_NEAR_ZERO_A   2.5f                                                   
    #define IO_NEAR_PEAK_A   2.5f                                  
    #define EDGE_MAX_TICKS   5U                                                  
                                                                          
    /* 电流状态：LOW低平台、RISE上升、HIGH高平台、FALL下降、WAIT中间过渡。 */
    #define PHASE_LOW        0U  
    #define PHASE_RISE       1U
    #define PHASE_HIGH       2U
    #define PHASE_FALL       3U
    #define PHASE_WAIT       4U                                                
    static uint16_t IoDetectorInitialized = 0U;

    volatile uint16_t IoPhase = PHASE_LOW;                
    static uint16_t EdgeTicks = 0U;                          
    
    /* 窗口剩余更新次数；EdgeDuty在进入边沿时锁存，窗口结束后停止使用。 */
    volatile uint16_t AnnHoldRemaining = 0U;                                        
    static uint16_t EdgeUseAnn = 0U;                                           
    static float EdgeDuty = 0.0f;                                           
    static float LastIo = 0.0f;                                

    /* 时间诊断单位us；ADC指标覆盖C函数主体，模型调用时间含中断抢占。 */
    volatile float g_adcIsrTimeUs = 0.0f;                                
    volatile float g_annTimeUs = 0.0f;                                                    

    /* 启动顺序：设备/PIE/板级外设 → 采样与控制状态 → 计时及后台模块 → 开中断。
     * 主循环保持轮询方式；ADC中断可抢占前台计算。 */
    void main(void)
    {
          
        Device_init();

        Device_initGPIO();

        Interrupt_initModule();

        Interrupt_initVectorTable();

        Board_init();
                                                                
        ADC_setInterruptPulseMode(ADCA_BASE, ADC_PULSE_END_OF_CONV);
        ADC_Sample_Init();
        Controller_Init();
        NN_initTiming();                        

        CapMonitor_Init();
        CapSCI_Init();
        PortalSCI_init();                                            

        C2000Ware_libraries_init();

        EINT;
        ERTM;

        while(1) /* 前台持续轮询；实时控制由ADC中断执行，此循环不是等待某个状态的阻塞循环。 */
        {
            if (AnnStartEnable != 0U && AnnUpdateReq != 0U) /* 两个条件同时满足才推理：电压启动标志已置位，并且至少一个方向有待处理请求。 */
            {
                float nnPeak, nnTime, nnVc1, nnVs1;
                uint16_t nnDirection, nnRequest, nnOK;

                /* 短临界区复制一个方向的输入并清除该请求；推理调用在开中断状态执行。 */
                DINT;
                if ((AnnUpdateReq & ANN_RISE_REQ) != 0U) /* 检测请求位bit0；两方向同时有请求时优先处理上升沿，下降沿请求保留。 */
                {
                    nnDirection = 0U;
                    nnRequest = ANN_RISE_REQ;
                }
                else
                {
                    nnDirection = 1U;
                    nnRequest = ANN_FALL_REQ;
                }
                nnPeak = AnnCapturedPeak[nnDirection];
                nnTime = AnnCapturedTime[nnDirection];
                nnVc1  = AnnCapturedVc1[nnDirection];
                nnVs1  = AnnCapturedVs1[nnDirection];
                AnnUpdateReq &= (uint16_t)(~nnRequest);
                EINT;

                /* 模型输入顺序：峰值A、时间s、物理Vc1电压、Vs1/VG1电压、方向0/1。 */
                g_nnInput[0] = nnPeak;
                g_nnInput[1] = nnTime;
                g_nnInput[2] = nnVc1;
                g_nnInput[3] = nnVs1;
                g_nnInput[4] = (float)nnDirection;
                NN_runOnce();
                g_annTimeUs = g_nnTimeUs;                                  

                nnOK = (g_nnReturnCode == 0 && /* 成功条件为返回码为0，且输出在[0,0.95]内；任一比较不成立均不更新占空比缓存。 */
                        g_nnOutput[0] >= 0.0f &&
                        g_nnOutput[0] <= 0.95f);

                DINT;
                                                                                                             
                /* 仅发布成功且当前没有同方向更新请求的结果；新请求留待下次处理。 */
                if (nnOK && (AnnUpdateReq & nnRequest) == 0U) /* 本次输出有效且推理期间没有新的同方向请求时，才发布结果；否则保留现有缓存。 */
                {
                    if (nnDirection == 0U) /* 0为上升沿：更新上升沿占空比及有效位；另一分支更新下降沿缓存。 */
                    {
                        AnnDutyRise = g_nnOutput[0];
                        AnnDutyRiseValid = 1U;
                    }
                    else
                    {
                        AnnDutyFall = g_nnOutput[0];
                        AnnDutyFallValid = 1U;
                    }
                }
                if (AnnDutyRiseValid != 0U && AnnDutyFallValid != 0U) /* 两方向均有成功结果后置就绪标志；只有一个方向有效时不进入就绪状态。 */
                {
                                                                                   
                    AnnInferenceReady = 1U;
                }
                EINT;
            }
                                                                                    
            /* 无待处理请求时推进一次CNN后台步骤及串口轮询，避免长时间占用前台。 */
            if (AnnUpdateReq == 0U) CapBackground_Task(AnnStartEnable); /* 无待推理请求才执行一次CNN后台步骤；启动标志同时传入辨识调度器。 */
            if (AnnUpdateReq == 0U) PortalSCI_poll(); /* 再次检查请求位；若后台执行期间中断产生新请求，本次跳过串口轮询。 */
        }
    }

    /* ADC实时入口：读取码值 → 标定与滤波 → 边沿状态机 → 双环PI → PWM。
     * 末尾将原始三通道样本交给CNN采样器，并维护中断标志及耗时统计。 */
    __interrupt void INT_myADC0_1_ISR(void)
    {
                                        
        float ioNow, ioStep; uint16_t useANN;
        uint16_t annEdgeDetected = 0U;
        uint16_t annEdgeDirection = 0U;
        uint16_t annEdgeRequest;
        uint32_t adcIsrStart;

        adcIsrStart = CPUTimer_getTimerCount(CPUTIMER0_BASE);

        i++;
                             
        /* 通道映射：字段Vc1/Vc2对应硬件Vs1/Vs2；字段Vs1/Vs2对应硬件Vc1/Vc2。
         * Vin、iL1、iL2、io、ic分别读取其配置的SOC结果。 */
        ADC_Sample_Raw.Vc1 = ADC_readResult(ADCBRESULT_BASE, ADC_SOC_NUMBER0);                              
        ADC_Sample_Raw.Vc2 = ADC_readResult(ADCARESULT_BASE, ADC_SOC_NUMBER0);                              
        ADC_Sample_Raw.Vin = ADC_readResult(ADCBRESULT_BASE, ADC_SOC_NUMBER1);             
        ADC_Sample_Raw.iL1 = ADC_readResult(ADCBRESULT_BASE, ADC_SOC_NUMBER2);             
        ADC_Sample_Raw.iL2 = ADC_readResult(ADCARESULT_BASE, ADC_SOC_NUMBER1);             
        ADC_Sample_Raw.io  = ADC_readResult(ADCARESULT_BASE, ADC_SOC_NUMBER2);             
        ADC_Sample_Raw.Vs1 = ADC_readResult(ADCBRESULT_BASE, ADC_SOC_NUMBER3);         
        ADC_Sample_Raw.Vs2 = ADC_readResult(ADCARESULT_BASE, ADC_SOC_NUMBER3);         
        ADC_Sample_Raw.ic  = ADC_readResult(ADCARESULT_BASE, ADC_SOC_NUMBER4);        

        /* 物理量换算：(Raw-Offset)*Gain；iL1/iL2额外取负以匹配电流方向。 */
        ADC_Sample_RealValue.Vc1 = ((float)ADC_Sample_Raw.Vc1 - ADC_Sample_Offset.Vc1) * ADC_Sample_Gain.Vc1;
        ADC_Sample_RealValue.Vc2 = ((float)ADC_Sample_Raw.Vc2 - ADC_Sample_Offset.Vc2) * ADC_Sample_Gain.Vc2;
        ADC_Sample_RealValue.Vin = ((float)ADC_Sample_Raw.Vin - ADC_Sample_Offset.Vin) * ADC_Sample_Gain.Vin;
        ADC_Sample_RealValue.iL1 = - ((float)ADC_Sample_Raw.iL1 - ADC_Sample_Offset.iL1) * ADC_Sample_Gain.iL1;                                       
        ADC_Sample_RealValue.iL2 = - ((float)ADC_Sample_Raw.iL2 - ADC_Sample_Offset.iL2) * ADC_Sample_Gain.iL2;                                       
        ADC_Sample_RealValue.io  = ((float)ADC_Sample_Raw.io  - ADC_Sample_Offset.io ) * ADC_Sample_Gain.io;
        ADC_Sample_RealValue.Vs1 = ((float)ADC_Sample_Raw.Vs1 - ADC_Sample_Offset.Vs1) * ADC_Sample_Gain.Vs1;
        ADC_Sample_RealValue.Vs2 = ((float)ADC_Sample_Raw.Vs2 - ADC_Sample_Offset.Vs2) * ADC_Sample_Gain.Vs2;
        ADC_Sample_RealValue.ic  = ((float)ADC_Sample_Raw.ic - ADC_Sample_Offset.ic) * ADC_Sample_Gain.ic;

        /* 一阶递推滤波：y += alpha*(x-y)；Ioav是io的独立慢速平均反馈。 */
        ADC_Sample_Filtered.Vc1 += LPF_ALPHA_10Hz * (ADC_Sample_RealValue.Vc1 - ADC_Sample_Filtered.Vc1);
        ADC_Sample_Filtered.Vc2 += LPF_ALPHA_10Hz * (ADC_Sample_RealValue.Vc2 - ADC_Sample_Filtered.Vc2);
        ADC_Sample_Filtered.Vin += LPF_ALPHA_10Hz * (ADC_Sample_RealValue.Vin - ADC_Sample_Filtered.Vin);
        ADC_Sample_Filtered.iL1 += LPF_ALPHA_10kHz * (ADC_Sample_RealValue.iL1 - ADC_Sample_Filtered.iL1);
        ADC_Sample_Filtered.iL2 += LPF_ALPHA_10kHz * (ADC_Sample_RealValue.iL2 - ADC_Sample_Filtered.iL2);
        ADC_Sample_Filtered.io  += LPF_ALPHA_10kHz * (ADC_Sample_RealValue.io  - ADC_Sample_Filtered.io);
        ADC_Sample_Filtered.Ioav += LPF_ALPHA_10Hz * (ADC_Sample_RealValue.io  - ADC_Sample_Filtered.Ioav);
        ADC_Sample_Filtered.Vs1 += LPF_ALPHA_10Hz * (ADC_Sample_RealValue.Vs1 - ADC_Sample_Filtered.Vs1);     
        ADC_Sample_Filtered.Vs2 += LPF_ALPHA_10Hz * (ADC_Sample_RealValue.Vs2 - ADC_Sample_Filtered.Vs2);     
        ADC_Sample_Filtered.ic  += LPF_ALPHA_10kHz * (ADC_Sample_RealValue.ic  - ADC_Sample_Filtered.ic);

        /* 电压启动门槛只置位一次，后续不随Vin回落自动清零。 */
        if (AnnStartEnable == 0U && ADC_Sample_Filtered.Vin >= 265.0f) /* 尚未启动且滤波Vin达到265V时置位；已经启动后不在此处撤销标志。 */
            AnnStartEnable = 1U;

        /* 边沿检测使用当拍换算电流，不使用Filtered.io；首次只建立参考状态。 */
        ioNow = ADC_Sample_RealValue.io;
        if (IoDetectorInitialized == 0U) /* 仅首次采样建立LastIo与初始平台状态，避免将启动时已有电流误当成一次跳变。 */
{
    LastIo = ioNow;
    ioStep = 0.0f;

    if (ioNow <= IO_LOW_A + IO_NEAR_ZERO_A) /* 首次电流不高于低平台边界时从LOW开始；当前参数下边界为1+2.5=3.5A。 */
        IoPhase = PHASE_LOW;
    else if (ioNow >= Io_Peak - IO_NEAR_PEAK_A) /* 首次电流已达到高平台边界时从HIGH开始；当前参数下边界为18-2.5=15.5A。 */
        IoPhase = PHASE_HIGH;
    else
        IoPhase = PHASE_WAIT;

    IoDetectorInitialized = 1U;
}
else
{
    ioStep = ioNow - LastIo;
    LastIo = ioNow;
}

        /* 低平台检查上升沿，高平台检查下降沿；超时或反向变化进入WAIT。
         * WAIT需重新到达高/低平台后才能再次进入对应平台状态。 */
        switch (IoPhase) /* 仅执行当前状态的分支；break结束本次状态处理，新状态通常由下一次采样继续判断。 */
    {
    case PHASE_LOW: /* 低平台等待上升：同时使用电流跨界和相邻采样增量两个判据。 */
                                                                                    
        if (ioNow > IO_LOW_A + IO_NEAR_ZERO_A && /* 必须既离开低平台、又比上一拍增加超过2A，才产生上升沿事件。 */
            ioStep > IO_CHANGE_THRESHOLD) /* LOW中相邻采样增量严格超过2A，配合离开低平台条件判定上升沿。 */
        {
        AnnRiseTriggerIo = ioNow;
        AnnRiseTriggerStep = ioStep;
        AnnRisePreviousIo = ioNow - ioStep;

            annEdgeDetected = 1U;
            annEdgeDirection = 0U;
            IoPhase = PHASE_RISE;
            EdgeTicks = 0U;
            if (ioNow >= Io_Peak - IO_NEAR_PEAK_A) /* 上升事件当拍若已到高平台，直接进入HIGH；否则保持RISE继续跟踪。 */
            {
                IoPhase = PHASE_HIGH;
            }
        }
        else if (ioNow >= Io_Peak - IO_NEAR_PEAK_A) /* 未满足上升跳变条件但电流已在高平台，则直接进入HIGH，不在此分支生成边沿事件。 */
            IoPhase = PHASE_HIGH;
        break;

    case PHASE_RISE: /* 跟踪已触发的上升过程：达到高平台则完成，否则检查跟踪超时或方向反转。 */
    if (ioNow >= Io_Peak - IO_NEAR_PEAK_A) /* 处于RISE时优先检查是否到达高平台；满足后转HIGH，不再走本次超时判断。 */
    {
        IoPhase = PHASE_HIGH;
    }
    else if (EdgeTicks >= EDGE_MAX_TICKS || /* 尚未到高平台时，只要已跟踪至少5拍或出现明显反向下降，就转WAIT。 */
             ioStep < -IO_CHANGE_THRESHOLD) /* RISE中相邻电流下降超过2A，视为不再持续上升。 */
    {
        IoPhase = PHASE_WAIT;
    }
    break;

    case PHASE_HIGH: /* 高平台等待下降：先检查下降事件，再判断是否直接落到低平台。 */
                                                                                   
        if (ioNow < Io_Peak - IO_NEAR_PEAK_A && /* 必须既低于高平台边界、又比上一拍下降超过2A，才产生下降沿事件。 */
            ioStep < -IO_CHANGE_THRESHOLD) /* 与上一拍相比下降超过2A；必须同时满足上行的电流离开高平台条件，才触发下降沿。 */
        {
            annEdgeDetected = 1U;
            annEdgeDirection = 1U;
            IoPhase = PHASE_FALL;
            EdgeTicks = 0U;
            if (ioNow <= IO_LOW_A + IO_NEAR_ZERO_A) /* 下降事件当拍若已经回到低平台，直接进入LOW；否则保持FALL继续跟踪。 */
            {
                IoPhase = PHASE_LOW;
            }
        }
        else if (ioNow <= IO_LOW_A + IO_NEAR_ZERO_A) /* 未满足下降事件条件但电流已处于低平台时直接转LOW，不额外产生下降事件。 */
            IoPhase = PHASE_LOW;
        break;

    case PHASE_FALL: /* 跟踪已触发的下降过程：回到低平台则完成，否则检查超时或方向反转。 */
    if (ioNow <= IO_LOW_A + IO_NEAR_ZERO_A) /* FALL中电流回到低平台时转LOW，结束本次下降跟踪。 */
    {
        IoPhase = PHASE_LOW;
    }
    else if (EdgeTicks >= EDGE_MAX_TICKS || /* 尚未到低平台时，只要已跟踪至少5拍或出现明显反向上升，就转WAIT。 */
             ioStep > IO_CHANGE_THRESHOLD) /* FALL中相邻电流增加超过2A，视为不再持续下降。 */
    {
        IoPhase = PHASE_WAIT;
    }
    break;

    case PHASE_WAIT: /* 处于中间电流且未建立可靠平台状态；只恢复平台状态，不在此处触发新边沿。 */
    if (ioNow <= IO_LOW_A + IO_NEAR_ZERO_A) /* WAIT优先检查低平台；回到低平台后转LOW，重新等待上升。 */
        IoPhase = PHASE_LOW;
    else if (ioNow >= Io_Peak - IO_NEAR_PEAK_A) /* WAIT电流未到低平台但已达到高平台时转HIGH；两者都不满足则继续WAIT。 */
        IoPhase = PHASE_HIGH;
    break;

    }

    /* 新边沿锁存方向、占空比和输入快照；快照更新不等待前台推理完成。 */
    if (annEdgeDetected != 0U) /* 只有本拍产生了边沿事件，才刷新方向、窗口、PI记录起点及对应输入请求。 */
    {
        AnnRiseDetectCount++;
        AnnDirection = annEdgeDirection;
                                                                                                                 
        EdgeDuty = (annEdgeDirection == 0U) ? AnnDutyRise : AnnDutyFall; /* 按本次边沿方向选择已有缓存：0选上升沿，1选下降沿。 */
        if (annEdgeDirection == 0U) /* 按当前边沿方向选择对应占空比处理分支。 */
            EdgeDuty -= AnnRiseDutyCorrection;
        else
            EdgeDuty -= AnnFallDutyCorrection;
        EdgeUseAnn = (AnnInferenceReady != 0U && /* 缓存整体就绪且本次候选占空比在[0,0.95]内时，窗口选择标志才有效。 */
                      EdgeDuty >= 0.0f && EdgeDuty <= 0.95f);
        AnnHoldRemaining = EdgeUseAnn ? /* 选择标志有效才设置保持次数；否则窗口次数为0。 */
                   ((annEdgeDirection == 0U) ? /* 方向0取2次更新，方向1取3次更新；检测当拍也会在中断末尾扣除一次。 */
                    ANN_RISE_HOLD_CYCLES : ANN_FALL_HOLD_CYCLES) : 0U;

        if (annEdgeDirection == 0U) /* 只清空当前方向的PI记录计数，另一个方向上一次记录不受影响。 */
            AnnRisePiTraceCount = 0U;
        else
            AnnFallPiTraceCount = 0U;

        AnnPiOutputBeforeReplace = CurrCon_Reg.Output;
        AnnPiOutputAtEdge[annEdgeDirection] = CurrCon_Reg.Output;

        if (AnnStartEnable != 0U) /* 达到启动门槛后，当前边沿才锁存模型输入并置推理请求；此前仅更新边沿与PI状态。 */
        {
        
            annEdgeRequest = (annEdgeDirection == 0U) ? ANN_RISE_REQ : ANN_FALL_REQ; /* 按方向选择请求位；后续按位或置位，不会清除另一方向尚未处理的请求。 */
            AnnCapturedPeak[annEdgeDirection] = Io_Peak;
            AnnCapturedTime[annEdgeDirection] = Rise_time;
                                                                                        
            AnnCapturedVc1[annEdgeDirection] = ADC_Sample_Filtered.Vs1;
            AnnCapturedVs1[annEdgeDirection] = ADC_Sample_Filtered.Vc1;
            AnnUpdateReq |= annEdgeRequest;
        }
    }

    if (IoPhase == PHASE_RISE || IoPhase == PHASE_FALL) /* 只在当前状态仍为RISE或FALL时累计跟踪拍数，进入平台或WAIT后不在此处递增。 */
     {   EdgeTicks++;
     AnnRiseWriteCount++;
     }
    if (AnnInferenceReady == 0U) /* 两方向缓存尚未就绪时，撤销窗口选择并清零保持次数，继续采用PI候选输出。 */
    {
        EdgeUseAnn = 0U;
        AnnHoldRemaining = 0U;
    }
                                                                       
    /* 本实现每拍继续计算PI；最终PWM候选值在比较寄存器写入前选择。 */
    useANN= 0U;

        /* 电压环：参考为0.75*Vin，反馈为Filtered.Vc1；增量式PI输出限于[-0.8,0.8]。 */
        VoltCon_Reg.Ref = 0.75f * ADC_Sample_Filtered.Vin;
        VoltCon_Reg.Err_Old = VoltCon_Reg.Err_New;
        VoltCon_Reg.Err_New = VoltCon_Reg.Ref - ADC_Sample_Filtered.Vc1;    
                                 
        if(useANN == 0U) /* 本拍useANN已设为0，因此执行电压环增量PI更新。 */
        {
        VoltCon_Reg.Output = VoltCon_Reg.Output + (VoltCon_Reg.Kp + VoltCon_Reg.Ki) * VoltCon_Reg.Err_New - VoltCon_Reg.Kp * VoltCon_Reg.Err_Old;
        }

        if (VoltCon_Reg.Output >= 0.8f) /* 电压环输出达到或超过上限时钳位到0.8，限制给电流参考的调节量。 */
        {
                VoltCon_Reg.Output = 0.8f;
        }
        if (VoltCon_Reg.Output <= -0.8f) /* 电压环输出达到或低于下限时钳位到-0.8；上下限判断分别执行。 */
        {
                VoltCon_Reg.Output = -0.8f;
        }

        /* 电流环：参考由负载脉动项和电压环输出构成，反馈为Filtered.iL1；
         * 增量式PI输出限制在[0,0.95]，用于占空比换算。 */
        CurrCon_Reg.Ref = 0.5f * (ADC_Sample_Filtered.io - ADC_Sample_Filtered.Ioav) - VoltCon_Reg.Output;
        CurrCon_Reg.Err_Old = CurrCon_Reg.Err_New;
        CurrCon_Reg.Err_New =  CurrCon_Reg.Ref - ADC_Sample_Filtered.iL1;

        if (useANN != 0U)  /* 非零时该分支直接使用EdgeDuty；本实现本拍useANN为0，执行下方电流环PI分支。 */
        {
            CurrCon_Reg.Output = EdgeDuty;
        }
        else 
        {
        CurrCon_Reg.Output = CurrCon_Reg.Output + (CurrCon_Reg.Kp + CurrCon_Reg.Ki) * CurrCon_Reg.Err_New - CurrCon_Reg.Kp * CurrCon_Reg.Err_Old;    
        }


        if (CurrCon_Reg.Output >= 0.95f) /* 电流环占空比上限为0.95；达到或超过上限时钳位。 */
        {
                CurrCon_Reg.Output = 0.95f;
        }
        if (CurrCon_Reg.Output <= 0.00f) /* 电流环占空比下限为0；计算结果为零或负数时钳位到0。 */
        {
                CurrCon_Reg.Output = 0.00f;
        }

        /* 将归一化占空比乘周期计数并四舍五入；下方条件决定是否采用边沿缓存。 */
        uint16_t CMPA = (uint16_t)(CurrCon_Reg.Output * EPWM_TIMER_TBPRD + 0.5f);
        AnnPwmActive = 0U;
        if (0U && AnnInferenceReady != 0U && EdgeUseAnn != 0U && /* 首项0U使整个条件恒假，本分支当前不替换CMPA；后续条件依次为缓存就绪、窗口有效等。 */
            AnnHoldRemaining != 0U && /* 即使存在有效缓存，也要求本次窗口还有剩余保持次数。 */
            EdgeDuty >= 0.0f && EdgeDuty <= 0.95f) /* 候选占空比还必须处于[0,0.95]范围；以上各项均为与关系。 */
        {
                                                                                                            
            CMPA = (uint16_t)(EdgeDuty * EPWM_TIMER_TBPRD + 0.5f);
            AnnPwmActive = 1U;
            count++;
        }

        /* 两路PWM写入相同CMPA；寄存器的影子装载时刻由外设配置决定。 */
        EPWM_setCounterCompareValue(EPWM1_BASE,EPWM_COUNTER_COMPARE_A,CMPA);
        EPWM_setCounterCompareValue(EPWM2_BASE,EPWM_COUNTER_COMPARE_A,CMPA);
                                                                                                                                             
        /* 记录本拍已计算并限幅的PI输出；每个方向最多保存ANN_PI_TRACE_LEN点。 */
        if (AnnPwmActive != 0U) /* 仅在本拍实际采用边沿占空比写入PWM时，才采集该窗口对应的PI输出记录。 */
        {
            if (AnnDirection == 0U) /* 当前方向为上升沿，记录到上升沿数组。 */
            {
                if (AnnRisePiTraceCount < ANN_PI_TRACE_LEN) /* 有效长度未到5时才写入并递增，防止上升沿记录数组越界；达到上限则不再写。 */
                {
                    AnnRisePiTrace[AnnRisePiTraceCount] = CurrCon_Reg.Output;
                    AnnRisePiTraceCount++;
                }
            }
            else if (AnnDirection == 1U) /* 上升沿条件不成立且方向为1时，记录到下降沿数组。 */
            {
                if (AnnFallPiTraceCount < ANN_PI_TRACE_LEN) /* 下降沿有效长度未到5时才写入并递增，达到上限则保留已有记录。 */
                {
                    AnnFallPiTrace[AnnFallPiTraceCount] = CurrCon_Reg.Output;
                    AnnFallPiTraceCount++;
                }
            }
        }
                                                          
        /* 完成本拍输出后消耗一次保持计数；归零时撤销本窗口选择标志。 */
        if (AnnHoldRemaining != 0U) /* 剩余次数大于0时才递减，避免无符号计数从0下溢。 */
        {
            AnnHoldRemaining--;
            if (AnnHoldRemaining == 0U) /* 本拍递减后归零，立即撤销窗口选择；下一拍不能继续使用本次窗口。 */
                EdgeUseAnn = 0U;
        }
                 
        /* CNN采样输入顺序：物理Vc1码值、ic码值、io码值；计时来自本次中断入口。 */
        CapMonitor_OnAdc((uint16_t)ADC_Sample_Raw.Vs1,
                         (uint16_t)ADC_Sample_Raw.ic,
                         (uint16_t)ADC_Sample_Raw.io, adcIsrStart,
                         ADC_getInterruptOverflowStatus(ADCA_BASE, ADC_INT_NUMBER1));
        if (ADC_getInterruptOverflowStatus(ADCA_BASE, ADC_INT_NUMBER1)) /* 仅检测到ADCA中断1溢出时清溢出标志；普通中断标志与PIE应答随后仍按顺序清除。 */
            ADC_clearInterruptOverflowStatus(ADCA_BASE, ADC_INT_NUMBER1);
        ADC_clearInterruptStatus(ADCA_BASE, ADC_INT_NUMBER1);
        Interrupt_clearACKGroup(INTERRUPT_ACK_GROUP1);

        /* Timer0向下计数；无符号差值换算为us，并更新已观测到的最大中断耗时。 */
        adcIsrStart = (uint32_t)(adcIsrStart -
                     CPUTimer_getTimerCount(CPUTIMER0_BASE));
        if (g_nnTimerClockHz != 0UL) /* 计时频率非零时才进行us换算，避免除零；为零时下方将耗时记为-1。 */
            g_adcIsrTimeUs = (float)adcIsrStart *
                            (1000000.0f / (float)g_nnTimerClockHz);
        else
            g_adcIsrTimeUs = -1.0f;
        if (g_adcIsrTimeUs > g_capAdcIsrMaxUs) /* 本次测得的中断耗时超过历史最大值时才更新最大值，不在此处清零历史记录。 */
            g_capAdcIsrMaxUs = g_adcIsrTimeUs;

    }

    /* 设置双环PI系数并清空误差、参考和输出状态；Ki按每次控制更新累加。 */
    void Controller_Init(void)
    {
        VoltCon_Reg.Kp          = 0.025f;
        VoltCon_Reg.Ki          = 0.000001f;
        VoltCon_Reg.Err_New     = 0.0f;
        VoltCon_Reg.Err_Old     = 0.0f;
        VoltCon_Reg.Ref         = 0.0f;
        VoltCon_Reg.Output      = 0.0f;

        CurrCon_Reg.Kp          = 0.0045f;        
        CurrCon_Reg.Ki          = 0.0008f;         
        CurrCon_Reg.Err_New     = 0.0f;
        CurrCon_Reg.Err_Old     = 0.0f;
        CurrCon_Reg.Ref         = 0.0f;
        CurrCon_Reg.Output      = 0.0f;
    }

    /* 初始化原始采样和各通道标定参数。Offset单位为ADC码，Gain为物理量/码；
     * 这些参数用于实时反馈换算，也会由CNN在请求开始时锁存。 */
    void ADC_Sample_Init(void)
    {
        ADC_Sample_Raw.Vc1 = 0.0;   
        ADC_Sample_Raw.Vc2 = 0.0;   
        ADC_Sample_Raw.Vin = 0.0;  
        ADC_Sample_Raw.iL1 = 0.0;   
        ADC_Sample_Raw.iL2 = 0.0;   
        ADC_Sample_Raw.io  = 0.0;   
        ADC_Sample_Raw.Vs1 = 0.0;   
        ADC_Sample_Raw.Vs2 = 0.0;
        ADC_Sample_Raw.ic  = 0.0;   


        ADC_Sample_Offset.Vc1 = 0.0;   
        ADC_Sample_Offset.Vc2 = 0.0;   
        ADC_Sample_Offset.Vin = 0.0;  
        ADC_Sample_Offset.iL1 = 2248;   
        ADC_Sample_Offset.iL2 = 2259;   
        ADC_Sample_Offset.io  = 0;   
        ADC_Sample_Offset.Vs1 = 0.0;   
        ADC_Sample_Offset.Vs2 = 0.0;
        ADC_Sample_Offset.ic  = 1467;   

        ADC_Sample_Gain.Vc1 = 0.12599f;   
        ADC_Sample_Gain.Vc2 = 0.12662f;   
        ADC_Sample_Gain.Vin = 0.12599f;  
        ADC_Sample_Gain.iL1 = 0.02517f;   
        ADC_Sample_Gain.iL2 = 0.02517f;   
        ADC_Sample_Gain.io  = 0.00724f;    
        ADC_Sample_Gain.Vs1 = 0.12599f;   
        ADC_Sample_Gain.Vs2 = 0.12599f;
        ADC_Sample_Gain.ic  = 0.022911f;                                     

    }
