#ifndef CAP_SCI_H
#define CAP_SCI_H
#include <stdint.h>
                                                                          
                                                   
/* 只读诊断：串口就绪、已发送CAP结果帧数及丢弃数。 */
extern volatile uint16_t g_capSciEnabled;
extern volatile uint32_t g_capTxFrames, g_capTxDropped;
extern char g_capTxText[256];                                               
void CapSCI_Init(void);
/* 主循环发布结果事件；本函数提交快照，发送过程由PortalSCI_poll逐步完成。 */
void CapSCI_Publish(void);
void CapSCI_Task(void);
#endif
