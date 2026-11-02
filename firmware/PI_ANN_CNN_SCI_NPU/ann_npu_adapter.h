#ifndef ANN_NPU_ADAPTER_H
#define ANN_NPU_ADAPTER_H
#include <stdint.h>
                                                                         
                                                                 
                                                                                   
                                                                    
int16_t ann_npu_start(const float raw_input[5]);
int16_t ann_npu_poll(float *dt_given);
#endif
