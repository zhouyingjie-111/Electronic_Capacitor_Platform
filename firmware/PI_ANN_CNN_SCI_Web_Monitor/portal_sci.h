#ifndef PORTAL_SCI_H_
#define PORTAL_SCI_H_
#include <stdint.h>

/* Default: on-board XDS110 UART, S2 SEL1=0, SEL2=0, J101 TX/RX fitted.
 * Set to 0 only for an external 3.3 V adapter wired RX=GPIO25, TX=GPIO29.
 * The default changes ONLY the SCI RX mux after Board_init; no SysConfig edit.
 */
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

/* A foreground snapshot; C is uF and ESR is mOhm, in physical P1..P4 order.
 * One active + one pending event keep a new capture from changing a frame
 * while it is being formatted. A replaced pending event is counted. */
typedef struct {
    uint32_t sequence, error_count;
    uint16_t state, valid, fault, warning, sample_count;
    float capacitance_uF[4], esr_mOhm[4];
    float sample_rate_hz, latency_us;
} PortalCapSnapshot;
void PortalSCI_publishCap(const PortalCapSnapshot *snapshot);
void PortalSCI_init(void);
void PortalSCI_poll(void);
#endif
