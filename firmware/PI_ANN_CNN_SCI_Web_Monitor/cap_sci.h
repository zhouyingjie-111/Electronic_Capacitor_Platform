#ifndef CAP_SCI_H
#define CAP_SCI_H
#include <stdint.h>
/* SCI hardware belongs exclusively to portal_sci.c. Do not enable the old
 * standalone CAP1 transmitter on the same UART. */
extern volatile uint16_t g_capSciEnabled;
extern volatile uint32_t g_capTxFrames, g_capTxDropped;
extern char g_capTxText[256]; /* Last CAP result frame, copied during TX. */
void CapSCI_Init(void);
void CapSCI_Publish(void);
void CapSCI_Task(void);
#endif
