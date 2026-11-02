#ifndef CAP_MONITOR_H
#define CAP_MONITOR_H
#include <stdint.h>
/* 状态机：IDLE空闲→CAPTURE采样→WAVELET小波→IMAGE成像→CNN推理→DONE完成；ERROR为错误出口。 */
enum { CAP_IDLE=0, CAP_CAPTURE=1, CAP_WAVELET=2, CAP_IMAGE=3,
       CAP_CNN=4, CAP_DONE=5, CAP_ERROR=6 };
/* 故障为可组合位掩码：溢出、触轨、时序、模型、配置、取消、未就绪。 */
enum { CAP_ERR_ADC_OVERFLOW=1, CAP_ERR_ADC_RAIL=2, CAP_ERR_TIMING=4,
       CAP_ERR_MODEL=8, CAP_ERR_CONFIG=16, CAP_ERR_CANCEL=32,
       CAP_ERR_NOT_READY=64 };
                                                                                
                                                                                 
/* 外部控制Request=1启动一次辨识；Valid仅表示本次结果有效，不表示精度保证。 */
extern volatile uint16_t g_capEnable, g_capRequest, g_capState, g_capValid;
extern volatile uint16_t g_capFault, g_capWarning, g_capSampleCount;
extern volatile uint32_t g_capSequence, g_capErrorCount, g_capAdcOverflowCount;
/* 四路结果固定对应P1~P4，不按数值大小排序；单位分别uF、Ohm、mOhm。 */
extern volatile float g_capC_uF[4], g_capESR_Ohm[4], g_capESR_mOhm[4];
extern volatile float g_capSampleRateHz, g_capMaxSampleGapUs;
extern volatile float g_capPreprocessUs, g_capInferUs, g_capTaskMaxUs;
extern volatile float g_capBackgroundMaxUs;
extern volatile float g_capLatencyUs, g_capAdcIsrMaxUs;
/* 三通道统计顺序为Vc1、ic、io；OffsetUsed/GainUsed是本次请求锁存的标定。 */
extern volatile float g_capInputMin[3], g_capInputMax[3], g_capInputMean[3];
extern volatile float g_capOffsetUsed[3], g_capGainUsed[3];
void CapMonitor_Init(void);
/* 主循环调用，converter_started为启动就绪条件；本函数不承担PWM控制。 */
void CapMonitor_Task(uint16_t converter_started);
                                                                       
void CapBackground_Task(uint16_t converter_started);
                                                                          
                                                              
                                                                
/* ADC中断末尾调用；三个输入均为原始ADC码值，entry_timer为中断入口时间戳。 */
void CapMonitor_OnAdc(uint16_t raw_voltage, uint16_t raw_ic, uint16_t raw_io,
                      uint32_t entry_timer, uint16_t overflow);
#endif
