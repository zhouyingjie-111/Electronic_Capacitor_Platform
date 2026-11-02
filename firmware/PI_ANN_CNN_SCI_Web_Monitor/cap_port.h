#ifndef CAP_PORT_H
#define CAP_PORT_H
#include <stdint.h>
#ifdef CAPMON_HOST_TEST
uint32_t CapPort_Timer(void);
uint32_t CapPort_Clock(void);
#else
#include "driverlib.h"
#include "device.h"
extern volatile uint32_t g_nnTimerClockHz;
static inline uint32_t CapPort_Timer(void) {
    return CPUTimer_getTimerCount(CPUTIMER0_BASE);
}
static inline uint32_t CapPort_Clock(void) { return g_nnTimerClockHz; }
#endif
#endif
