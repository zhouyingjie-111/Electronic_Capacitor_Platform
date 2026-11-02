#ifndef CAP_PORT_H
#define CAP_PORT_H
#include <stdint.h>
/* 计时适配层：主机测试可提供替代时钟；目标板读取CPU Timer0与计数频率Hz。 */
#ifdef CAPMON_HOST_TEST
uint32_t CapPort_Timer(void);
uint32_t CapPort_Clock(void);
#else
#include "driverlib.h"
#include "device.h"
extern volatile uint32_t g_nnTimerClockHz;
/* Timer0向下计数，耗时使用无符号start-end计算；调用方负责换算为us。 */
static inline uint32_t CapPort_Timer(void) {
    return CPUTimer_getTimerCount(CPUTIMER0_BASE);
}
static inline uint32_t CapPort_Clock(void) { return g_nnTimerClockHz; }
#endif
#endif
