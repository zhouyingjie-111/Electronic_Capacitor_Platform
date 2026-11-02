                                                                             
  
                         
  
                                                     
  
                                                                             
  
  
   
                         
  
// Copyright (C) 2024-2025 Texas Instruments Incorporated - http://www.ti.com
//
// Redistribution and use in source and binary forms, with or without 
// modification, are permitted provided that the following conditions 
// are met:
// 
//   Redistributions of source code must retain the above copyright 
//   notice, this list of conditions and the following disclaimer.
// 
//   Redistributions in binary form must reproduce the above copyright
//   notice, this list of conditions and the following disclaimer in the 
//   documentation and/or other materials provided with the   
//   distribution.
// 
//   Neither the name of Texas Instruments Incorporated nor the names of
//   its contributors may be used to endorse or promote products derived
//   from this software without specific prior written permission.
// 
// THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS 
// "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT 
// LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
// A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT 
// OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, 
// SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT 
// LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
// DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
// THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT 
// (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE 
// OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
// $
//###########################################################################

  
           
  
#include <stdbool.h>
#include <stdint.h>
#include "driverlib.h"
#include "nn_npu_platform.h"
#include "nn_cpu_test.h"

                                                                        
                                                                          
extern void tvmgen_default_IRQHandlerHelper(void);
extern __interrupt void tvmgen_default_IRQHandler(void);
volatile uint32_t g_nnNpuIrqCount = 0UL;
volatile uint16_t g_nnNpuInitOK = 0U;
static volatile uint16_t npuEventPending = 0U;
static uint16_t npuVectorInstalled = 0U;
static uint16_t npuHardwareAttempted = 0U;

#pragma FUNCTION_OPTIONS(NN_NPU_interrupt, "--opt_level=2")
__interrupt void NN_NPU_interrupt(void)
{
                                                                              
                                                                            
    g_nnNpuIrqCount++;
    npuEventPending = 1U;
    HWREGH(PIECTRL_BASE + PIE_O_ACK) = INTERRUPT_ACK_GROUP9;
}

void NN_NPU_service(void)
{
                                                                         
                                                                             
    if (npuEventPending != 0U)
    {
        npuEventPending = 0U;
        tvmgen_default_IRQHandlerHelper();
    }
}

  
          
  
#define NPU_O_CTL5      0x20U                                
#define NPU_O_IMASK     0x08U                              

#pragma FUNCTION_OPTIONS(NPU_setInstrMem, "--opt_level=3 --opt_for_speed=5")
#pragma FUNCTION_OPTIONS(NPU_setParamMem, "--opt_level=3 --opt_for_speed=5")

                                                                   
inline uint32_t NPU_getAccReg(void)
{
    return (ACC_BASE);
}

                                                       
inline uint32_t NPU_getCtlReg(void)
{
    return (IPSPECIFIC_BASE + NPU_O_CTL5);
}


                                                                 
void NPU_setInstrMem(uint32_t byteOffset, uint32_t num, const uint32_t *src)
{
    volatile uint32_t *ins = (uint32_t *) (INSTRUCTION_BASE + byteOffset);

    int count_by_4 = num >> 2;
    int count_by_1 = num & 0x3;

    if (count_by_4 > 0)
    {
        while (count_by_4--)
        {
            * ins    = *src++;
            *(ins+2) = *src++;
            *(ins+4) = *src++;
            *(ins+6) = *src++;
            ins   += 8;                                                                  
        }
    }
    if (count_by_1 > 0)
    {
        while (count_by_1--)
        {
            *ins++ = *src++;
            ins++;                                                                        
        }
    }
}

                                                               
void NPU_setParamMem(uint32_t byteOffset, uint32_t num, const uint32_t *src)
{
    volatile uint32_t *params = (uint32_t *) (RFDATA_BASE + byteOffset);

    int count_by_4 = num >> 2;
    int count_by_1 = num & 0x3;

    if (count_by_4 > 0)
    {
        while (count_by_4--)
            {
            * params    = *src++;
            *(params+2) = *src++;
            *(params+4) = *src++;
            *(params+6) = *src++;
            params   += 8;                                                                      
            }
    }
    if (count_by_1 > 0)
    {
        while (count_by_1--)
        {
            *params++ = *src++;
            params++;                                                                      
        }
    }
}

                                          
void NPU_setInterruptHandler(void (*handler)(void))
{
    bool disabled;
    (void)handler;
                                                                                   
                                                                                  
    if(npuVectorInstalled) return;
    disabled=Interrupt_disableGlobal();
    Interrupt_disable(INT_NPU);
    Interrupt_register(INT_NPU, &NN_NPU_interrupt);
    Interrupt_enable(INT_NPU);
    npuVectorInstalled=1U;
    if(!disabled) Interrupt_enableGlobal();
}

                                     
void NPU_enableInterrupt(void)
{
    HWREG(IPSTANDARD_BASE + NPU_O_IMASK) |= 0x1;
}

                                        
void NPU_clearInterruptACK(void)
{
                                                                             
                                                                           
}

void NN_NPU_prepareInterrupt(void)
{
    NPU_setInterruptHandler(&tvmgen_default_IRQHandler);
}

uint16_t NN_NPU_initHardware(void)
{
    uint32_t start=CPUTimer_getTimerCount(CPUTIMER0_BASE);
    if (npuHardwareAttempted != 0U) return g_nnNpuInitOK;
    npuHardwareAttempted=1U;
    g_nnNpuInitOK=0U;
    npuEventPending=0U;
    g_nnBootStage=5U;
    SysCtl_enablePeripheral(SYSCTL_PERIPH_CLK_NPU);
    g_nnBootStage=6U;
    SysCtl_setNPUClockDivider(SYSCTL_NPUCLK_DIV_1);
                                                                            
                                                                             
                                                                             
                                                  
    SysCtl_resetPeripheral(SYSCTL_PERIPH_RES_NPU);
    g_nnBootStage=7U;
    MemCfg_initSections(MEMCFG_SECT_GS3);
    while(!MemCfg_getInitStatus(MEMCFG_SECT_GS3))
        if((uint32_t)(start-CPUTimer_getTimerCount(CPUTIMER0_BASE)) > g_nnTimerClockHz/1000UL) return 0U;
    g_nnBootStage=8U;
    EALLOW;
    HWREG(MEMCFG_BASE+MEMCFG_O_GSXACCPROT0) &= ~MEMCFG_GSXACCPROT0_NPU_WRPROT_GS3;
    EDIS;
    g_nnBootStage=9U;
    NN_NPU_prepareInterrupt();
    NPU_enableInterrupt();
    g_nnNpuInitOK=1U;
    g_nnBootStage=10U;
    return 1U;
}

void NN_NPU_stopOnFault(void)
{
                                                                            
                                                                             
                                                                               
    HWREG(IPSTANDARD_BASE + NPU_O_IMASK) &= ~1UL;
    npuEventPending=0U;
}
