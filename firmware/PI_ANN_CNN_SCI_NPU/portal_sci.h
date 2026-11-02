#ifndef PORTAL_SCI_H_
#define PORTAL_SCI_H_
#include <stdint.h>

                                                                       
                                                                          
                                                                               
   
/* 串口接线选择：默认板载XDS110 UART；置0用于外接3.3V适配器。
 * 请按实际板卡开关和跳线连接，外接模式RX=GPIO25、TX=GPIO29。 */
#ifndef PORTAL_SCI_XDS110
#define PORTAL_SCI_XDS110 1
#endif
#define PORTAL_SCI_BAUD 115200UL

extern volatile uint32_t g_portalPollMaxCycles;
extern volatile uint32_t g_portalRxErrors;
extern volatile uint32_t g_portalBadCommands;
extern volatile uint32_t g_portalFramesSent;
extern volatile uint16_t g_portalReady;
extern volatile uint32_t g_portalCapEvents, g_portalCapFramesSent;
extern volatile uint32_t g_portalCapStatusFramesSent, g_portalCapDropped;

                                                                            
                                                                          
                                                                       
/* 辨识事件快照：结果按P1~P4排列，C单位uF、ESR单位mOhm；含状态、故障及采样统计。 */
typedef struct {
    uint32_t sequence, error_count;
    uint16_t state, valid, fault, warning, sample_count;
    float capacitance_uF[4], esr_mOhm[4];
    float sample_rate_hz, latency_us;
} PortalCapSnapshot;
/* 前台事件发布接口；输入快照会立即复制，不要求调用方长期保留对象。 */
void PortalSCI_publishCap(const PortalCapSnapshot *snapshot);
void PortalSCI_init(void);
/* 主循环周期调用；串口协议处理按有限步骤推进，不在ADC中断中调用。 */
void PortalSCI_poll(void);
#endif
