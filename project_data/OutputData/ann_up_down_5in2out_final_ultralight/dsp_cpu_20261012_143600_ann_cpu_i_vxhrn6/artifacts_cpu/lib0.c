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
    0x1.6082180000000p-9, 0x1.b4ba0e0000000p-6, 0x1.7a8efc0000000p-2, 0x1.fcb7620000000p-8, 0x1.8a328a0000000p-5, -0x1.6b79680000000p-4, -0x1.9acaae0000000p-2, 0x1.25efac0000000p-6, 
    -0x1.4c76fe0000000p+15, -0x1.8756c80000000p+11, -0x1.506f8e0000000p+11, 0x1.aa85860000000p+14, -0x1.abb3740000000p+13, -0x1.40f43a0000000p+14, 0x1.03e26e0000000p+12, -0x1.8632f00000000p+13, 
    0x1.cac99c0000000p+0, -0x1.beb4460000000p+0, -0x1.0410c20000000p+1, 0x1.a1ceb00000000p-4, 0x1.5f206a0000000p+0, 0x1.7f44800000000p+0, 0x1.3308ee0000000p-1, 0x1.3f84600000000p+0, 
    0x1.df392c0000000p+0, -0x1.809d4e0000000p+0, -0x1.c6213a0000000p-1, -0x1.a022720000000p-4, 0x1.4e60400000000p+0, -0x1.79e0880000000p+0, 0x1.9b84920000000p+0, 0x1.7b96760000000p+0, 
    -0x1.d313540000000p-1, 0x1.1731d40000000p+0, -0x1.4985900000000p+0, 0x1.075a5c0000000p-2, 0x1.00bd520000000p+0, 0x1.7a94c40000000p-1, -0x1.d32bfc0000000p-1, 0x1.4bf1a00000000p+0
  },
  .fused_constant_1_let = {
    0x1.7f1eb00000000p-1, -0x1.b3cec20000000p+0, 0x1.4a709e0000000p-1, -0x1.4662000000000p-6, -0x1.50c3f20000000p+0, 0x1.f76f300000000p-1, 0x1.d3b3e60000000p-2, 0x1.eab4380000000p-2, 
    0x1.4eb3e60000000p-2, 0x1.fb75ea0000000p-6, -0x1.bc87060000000p-2, -0x1.bffd340000000p-2, -0x1.14f3040000000p-2, 0x1.6403080000000p-2, -0x1.d28c820000000p+0, -0x1.4cb92a0000000p-4, 
    -0x1.362e240000000p+0, 0x1.f524d40000000p-4, 0x1.a2b0a60000000p-1, 0x1.2dd85a0000000p-1, 0x1.fe0ef80000000p-3, 0x1.7ef1b40000000p-5, -0x1.a8ee4a0000000p-3, 0x1.5f3ae40000000p-2, 
    -0x1.1305ec0000000p-7, -0x1.88137c0000000p-4, 0x1.364bd00000000p-3, -0x1.f177a40000000p-3, -0x1.290fc40000000p-1, 0x1.e976040000000p-3, 0x1.0968940000000p-2, 0x1.9b10880000000p-1
  },
  .fused_nn_contrib_dense_pack_constant_let = {
    -0x1.f1b6100000000p+8, 0x1.a5b9a60000000p+8, 0x1.35706c0000000p+8, 0x1.9650f00000000p+3, -0x1.6603f80000000p+8, 0x1.916fae0000000p+7, -0x1.6615f20000000p+8, -0x1.80d2b80000000p+8
  },
  .fused_constant_2_let = {
    -0x1.020bae0000000p-3, -0x1.2af0d80000000p-4, -0x1.4fd3b20000000p-6, 0x1.533e5a0000000p-4, 0x1.77dea20000000p-3, -0x1.c7fea40000000p-4, 0x1.c013e60000000p-4, 0x1.d539120000000p-6
  },
  .fused_nn_contrib_dense_pack_constant_1_let = {
    0x1.9e4cd20000000p-1, 0x1.3b64bc0000000p-5, -0x1.54ca700000000p-1, -0x1.ba71b40000000p-3
  },
  .fused_nn_contrib_dense_pack_constant_2_let = {
    0x1.2ec88a0000000p-2, 0x1.bd54360000000p-3
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