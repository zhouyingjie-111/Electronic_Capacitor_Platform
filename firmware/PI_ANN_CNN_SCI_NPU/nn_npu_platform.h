#ifndef NN_NPU_PLATFORM_H_
#define NN_NPU_PLATFORM_H_
#include <stdint.h>
                                                                         
void NN_NPU_prepareInterrupt(void);
                                                                 
uint16_t NN_NPU_initHardware(void);
                                                                
void NN_NPU_service(void);
void NN_NPU_stopOnFault(void);
extern volatile uint32_t g_nnNpuIrqCount;
extern volatile uint16_t g_nnNpuInitOK;
#endif
