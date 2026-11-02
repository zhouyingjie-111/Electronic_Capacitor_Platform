/*****************************************************************************/
/* Copyright (c) 2025 Texas Instruments Incorporated                         */
/* http://www.ti.com/                                                        */
/*                                                                           */
/*  Redistribution and  use in source  and binary forms, with  or without    */
/*  modification,  are permitted provided  that the  following conditions    */
/*  are met:                                                                 */
/*                                                                           */
/*     Redistributions  of source  code must  retain the  above copyright    */
/*     notice, this list of conditions and the following disclaimer.         */
/*                                                                           */
/*     Redistributions in binary form  must reproduce the above copyright    */
/*     notice, this  list of conditions  and the following  disclaimer in    */
/*     the  documentation  and/or   other  materials  provided  with  the    */
/*     distribution.                                                         */
/*                                                                           */
/*     Neither the  name of Texas Instruments Incorporated  nor the names    */
/*     of its  contributors may  be used to  endorse or  promote products    */
/*     derived  from   this  software  without   specific  prior  written    */
/*     permission.                                                           */
/*                                                                           */
/*  THIS SOFTWARE  IS PROVIDED BY THE COPYRIGHT  HOLDERS AND CONTRIBUTORS    */
/*  "AS IS"  AND ANY  EXPRESS OR IMPLIED  WARRANTIES, INCLUDING,  BUT NOT    */
/*  LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR    */
/*  A PARTICULAR PURPOSE ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT    */
/*  OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,    */
/*  SPECIAL,  EXEMPLARY,  OR CONSEQUENTIAL  DAMAGES  (INCLUDING, BUT  NOT    */
/*  LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,    */
/*  DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY    */
/*  THEORY OF  LIABILITY, WHETHER IN CONTRACT, STRICT  LIABILITY, OR TORT    */
/*  (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE    */
/*  OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.     */
/*                                                                           */
/*****************************************************************************/
#include "tvm/runtime/contrib/tvm_tinie_c28.h"
#include "tvm/runtime/c_runtime_api.h"
#ifdef __cplusplus
extern "C" {
#endif
__attribute__((section(".rodata.tvm"), ))
static const struct global_const_workspace {
  float fused_constant_let[40] __attribute__((aligned(16))); // 80 bytes, aligned offset: 0
  float fused_constant_1_let[32] __attribute__((packed, aligned(16))); // 64 bytes, aligned offset: 80
  float fused_nn_contrib_dense_pack_constant_let[8] __attribute__((packed, aligned(16))); // 16 bytes, aligned offset: 144
  float fused_constant_2_let[8] __attribute__((packed, aligned(16))); // 16 bytes, aligned offset: 160
  float fused_nn_contrib_dense_pack_constant_1_let[4] __attribute__((packed, aligned(16))); // 8 bytes, aligned offset: 176
  float fused_nn_contrib_dense_pack_constant_2_let[2] __attribute__((packed, aligned(16))); // 4 bytes, aligned offset: 192
} const global_const_workspace = {
  .fused_constant_let = {
    0x1.ca11b20000000p-5, 0x1.7888780000000p-4, 0x1.0baa800000000p-1, 0x1.0d61a40000000p-5, -0x1.5b39580000000p-5, -0x1.6be8020000000p-5, -0x1.3dcb3c0000000p-2, -0x1.2f76640000000p-5, 
    -0x1.6c80960000000p+15, -0x1.3d2fe20000000p+13, 0x1.c6e2060000000p+14, 0x1.25a3560000000p+14, -0x1.02f22a0000000p+6, -0x1.b577520000000p+14, 0x1.c529080000000p+13, -0x1.916efe0000000p+12, 
    0x1.20a2c00000000p-4, -0x1.146c080000000p-4, -0x1.69b85e0000000p-5, 0x1.65c1640000000p-6, 0x1.b567980000000p-5, 0x1.a6fb980000000p-5, 0x1.3baf220000000p-8, 0x1.00e0020000000p-5, 
    0x1.53115e0000000p-5, -0x1.003a180000000p-5, -0x1.6f56fe0000000p-5, -0x1.83a3300000000p-6, 0x1.ded0d40000000p-6, -0x1.892e200000000p-5, 0x1.e986260000000p-5, 0x1.a550980000000p-5, 
    -0x1.7a5a200000000p-2, 0x1.ce6cda0000000p-1, -0x1.aa6fc20000000p-1, 0x1.5f6bd20000000p-1, 0x1.9512dc0000000p-2, 0x1.0c34760000000p-1, -0x1.d4b73e0000000p-1, 0x1.5357c60000000p+0
  },
  .fused_constant_1_let = {
    0x1.7fd3580000000p-1, -0x1.41644e0000000p+0, 0x1.f139c60000000p-1, -0x1.26b06a0000000p-2, -0x1.33bd6e0000000p+0, 0x1.8437180000000p-1, 0x1.18177c0000000p-1, 0x1.2487fe0000000p-1, 
    0x1.8f0ef80000000p-6, -0x1.d6c1d40000000p-6, -0x1.c181780000000p-1, -0x1.18c6260000000p-2, 0x1.e67e560000000p-4, 0x1.f6ed020000000p-2, -0x1.84868e0000000p+0, -0x1.15be180000000p-1, 
    -0x1.8765660000000p-2, 0x1.4b6a660000000p-5, 0x1.cf02c80000000p-3, 0x1.d919020000000p-2, 0x1.80e6b40000000p-1, 0x1.399a580000000p-3, -0x1.762fe60000000p-1, 0x1.2ba7720000000p-2, 
    0x1.e9c0360000000p-3, 0x1.7188060000000p-2, 0x1.b68ac00000000p-4, -0x1.f156600000000p-2, -0x1.67aa480000000p-1, 0x1.8adb200000000p-4, 0x1.0251540000000p-5, 0x1.b0d7420000000p-1
  },
  .fused_nn_contrib_dense_pack_constant_let = {
    -0x1.6b9df40000000p+3, 0x1.283f060000000p+3, -0x1.b50dc00000000p-3, 0x1.4374b00000000p+1, -0x1.1849060000000p+3, 0x1.2709780000000p+3, -0x1.d31c8a0000000p+2, -0x1.6c6d620000000p+3
  },
  .fused_constant_2_let = {
    -0x1.4c6e540000000p-4, -0x1.b8900a0000000p-5, -0x1.7dcbf40000000p-6, 0x1.c001200000000p-5, 0x1.5fc9120000000p-3, -0x1.8bb86c0000000p-4, 0x1.fbf4aa0000000p-5, 0x1.6ebcde0000000p-5
  },
  .fused_nn_contrib_dense_pack_constant_1_let = {
    0x1.2922100000000p-1, -0x1.6521440000000p-4, -0x1.279e340000000p-2, -0x1.b30b400000000p-4
  },
  .fused_nn_contrib_dense_pack_constant_2_let = {
    0x1.4fded80000000p-2, 0x1.ea8e6a0000000p-3
  },
};// of total size 196 bytes
__attribute__((section(".bss.noinit.tvm"), aligned(16)))
static uint8_t global_workspace[32];
#include "tvmgen_default.h"
TVM_DLL int32_t tvmgen_default___tvm_main__(void* raw_input,void* output0,uint8_t* global_const_workspace_0_var,uint8_t* global_workspace_1_var);
int32_t tvmgen_default_run(struct tvmgen_default_inputs* inputs,struct tvmgen_default_outputs* outputs) {return tvmgen_default___tvm_main__(inputs->raw_input,outputs->output,((uint8_t*)&global_const_workspace),((uint8_t*)&global_workspace));
}
#ifdef __cplusplus
}
#endif
;