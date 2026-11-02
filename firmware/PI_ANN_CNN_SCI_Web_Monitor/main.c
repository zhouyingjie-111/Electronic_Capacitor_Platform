    //#############################################################################
    //
    // FILE:   empty_driverlib_main.c
    //
    //! \addtogroup driver_example_list
    //! <h1>Empty Project Example</h1> 
    //!
    //! This example is an empty project setup for Driverlib development.
    //!
    //
    //#############################################################################
    //
    //
    // 
    // C2000Ware v26.01.00.00
    //
    // Copyright (C) 2024 Texas Instruments Incorporated - http://www.ti.com
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
    //#############################################################################

    //
    // Included Files
    //
    #include "driverlib.h"
    #include "device.h"
    #include "board.h"
    #include "c2000ware_libraries.h"
    #include "main.h"
    #include "nn_cpu_test.h"
    #include "portal_sci.h"
    #include "cap_monitor.h"
    #include "cap_sci.h"
    //
    //Variation Defination
    //
    struct CON_REG VoltCon_Reg;
    struct CON_REG CurrCon_Reg;

    __uint16_t i= 0;

    struct ADC_Sample_Reg ADC_Sample_Offset;
    struct ADC_Sample_Reg ADC_Sample_Gain;
    struct ADC_Sample_Reg ADC_Sample_RealValue;
    struct ADC_Sample_Reg ADC_Sample_Raw;
    struct ADC_Sample_Reg ADC_Sample_Filtered;


    #define LPF_ALPHA_10kHz  0.628f  //fc=10kHz, fs=100kHz
    #define LPF_ALPHA_10Hz   0.0000628f  //fc=10kHz, fs=100kHz

    #define EPWM_TIMER_TBPRD 750

    //
    // Main
    //

    volatile float AnnRiseTriggerIo = 0.0f;    // 触发当拍电流，A
    volatile float AnnRiseTriggerStep = 0.0f;  // 触发当拍与上一拍的差值，A
    volatile float AnnRisePreviousIo = 0.0f;   // 上一拍电流，A
    volatile uint32_t AnnRiseDetectCount = 0UL;
    volatile uint32_t AnnRiseWriteCount  = 0UL;

    volatile float Io_Peak = 18.0f;       // 高电流平台：18 A  
    volatile uint16_t AnnUpdateReq = 0U;   // 位0：上升沿待推理；位1：下降沿待推理
    volatile float Rise_time = 0.00005f;  // 50 us，单位为秒

    volatile float AnnDutyRise = 0.0f;  // 上升沿的固定占空比

    volatile float AnnRiseDutyCorrection = -0.3f; // 上升沿校正量，0.01f表示1个百分点
    volatile float AnnFallDutyCorrection = 0.0f; // 下降沿校正量，与上升沿一样从ANN占空比中减去
    #define ANN_RISE_HOLD_CYCLES 2U // 上升沿：检测当次 + 后续2次中断
    #define ANN_FALL_HOLD_CYCLES 3U // 下降沿：检测当次 + 后续2次中断

    volatile float AnnDutyFall = 0.0f;  // 下降沿的固定占空比
    volatile uint16_t AnnStartEnable = 0U; // Vin滤波值首次达到270V后置1并保持
    volatile uint16_t AnnInferenceReady = 0U; // 1：两个方向均推理成功，缓存就绪
    volatile uint16_t AnnPwmActive = 0U; // 本次中断是否将ANN占空比写入CMP
    volatile float AnnPiOutputBeforeReplace = 0.0f; // 最近一次检测到边沿前一拍的PI占空比（保留原变量名）
    volatile float AnnPiOutputAtEdge[2] = {0.0f, 0.0f}; // [0]上升沿前一拍、[1]下降沿前一拍的PI占空比
    volatile float count = 0.0f;
    // ANN实际接管期间的电流环PI输出；每方向保存最近一次边沿，最多5点。
    #define ANN_PI_TRACE_LEN 5U
    volatile float AnnRisePiTrace[ANN_PI_TRACE_LEN] = {0.0f};
    volatile float AnnFallPiTrace[ANN_PI_TRACE_LEN] = {0.0f};
    volatile uint16_t AnnRisePiTraceCount = 0U;
    volatile uint16_t AnnFallPiTraceCount = 0U;

    // 边沿中断锁存输入；主循环仍通过 NN_runOnce() 推理。
    #define ANN_RISE_REQ  0x0001U
    #define ANN_FALL_REQ  0x0002U
    #define IO_LOW_A      1.0f
    volatile uint16_t AnnDirection = 0U; // 最近检测到的边沿：0=上升，1=下降
    volatile uint16_t AnnDutyRiseValid = 0U;
    volatile uint16_t AnnDutyFallValid = 0U;
    // 下标0：上升沿，下标1：下降沿。对应电压均为检测边沿当次ADC实测值。
    volatile float AnnCapturedPeak[2] = {0.0f, 0.0f};
    volatile float AnnCapturedTime[2] = {0.0f, 0.0f};
    volatile float AnnCapturedVc1[2] = {0.0f, 0.0f};
    volatile float AnnCapturedVs1[2] = {0.0f, 0.0f};

    #define IO_CHANGE_THRESHOLD   2.0f

    //边沿状态判断变量
    #define IO_NEAR_ZERO_A   2.5f // 保留原宏名：低平台容差，io <= 1.3 A
    #define IO_NEAR_PEAK_A   2.5f // 高平台容差，io >= 17.7 A
    #define EDGE_MAX_TICKS   5U   //检测到边缘后最多记录五次ADC中断
    //Io的四种阶段，低电流、上升沿、峰值平台、下降沿
    #define PHASE_LOW        0U  
    #define PHASE_RISE       1U
    #define PHASE_HIGH       2U
    #define PHASE_FALL       3U
    #define PHASE_WAIT 4U  // 电流处于中间值，暂时由 PI 控制
    static uint16_t IoDetectorInitialized = 0U;

    volatile uint16_t IoPhase = PHASE_LOW; // 当前阶段
    static uint16_t EdgeTicks = 0U;        // 已检测次数
    
    volatile uint16_t AnnHoldRemaining = 0U; // 当前保持窗口剩余更新次数
    static uint16_t EdgeUseAnn = 0U;       // 本次边沿是否使用ANN结果
    static float EdgeDuty = 0.0f;          // 进入边沿锁存的占空比
    static float LastIo = 0.0f;            // 上一次Io采样

    // 两个时间记录变量，单位：us；记录最近一次测量值。
    volatile float g_adcIsrTimeUs = 0.0f; // ADC中断C代码主体耗时
    volatile float g_annTimeUs = 0.0f;    // 模型调用耗时，含期间的中断抢占

    void main(void)
    {

        //
        // Initialize device clock and peripherals
        //
        Device_init();

        //
        // Disable pin locks and enable internal pull-ups.
        //
        Device_initGPIO();

        //
        // Initialize PIE and clear PIE registers. Disables CPU interrupts.
        //
        Interrupt_initModule();

        //
        // Initialize the PIE vector table with pointers to the shell Interrupt
        // Service Routines (ISR).
        //
        Interrupt_initVectorTable();

        //
        // PinMux and Peripheral Initialization
        //
        Board_init();
        /* Read SOC4 only after its conversion has completed. */
        ADC_setInterruptPulseMode(ADCA_BASE, ADC_PULSE_END_OF_CONV);
        ADC_Sample_Init();
        Controller_Init();
        NN_initTiming(); //神经网络初始化

        CapMonitor_Init();
        CapSCI_Init();
        PortalSCI_init(); /* Foreground SCI, no serial interrupts. */

        //
        // C2000Ware Library initialization
        //
        C2000Ware_libraries_init();

        //
        // Enable Global Interrupt (INTM) and real time interrupt (DBGM)
        //
        EINT;
        ERTM;

        while(1)
        {
            if (AnnStartEnable != 0U && AnnUpdateReq != 0U)
            {
                float nnPeak, nnTime, nnVc1, nnVs1;
                uint16_t nnDirection, nnRequest, nnOK;

                // 只在复制锁存输入及清除对应请求时短暂关中断。
                // 推理仍在开中断状态的主循环运行，保持原调用方式。
                DINT;
                if ((AnnUpdateReq & ANN_RISE_REQ) != 0U)
                {
                    nnDirection = 0U;
                    nnRequest = ANN_RISE_REQ;
                }
                else
                {
                    nnDirection = 1U;
                    nnRequest = ANN_FALL_REQ;
                }
                nnPeak = AnnCapturedPeak[nnDirection];
                nnTime = AnnCapturedTime[nnDirection];
                nnVc1  = AnnCapturedVc1[nnDirection];
                nnVs1  = AnnCapturedVs1[nnDirection];
                AnnUpdateReq &= (uint16_t)(~nnRequest);
                EINT;

                g_nnInput[0] = nnPeak;
                g_nnInput[1] = nnTime;
                g_nnInput[2] = nnVc1;
                g_nnInput[3] = nnVs1;
                g_nnInput[4] = (float)nnDirection;
                NN_runOnce();
                g_annTimeUs = g_nnTimeUs; // 复用原有模型调用计时

                nnOK = (g_nnReturnCode == 0 &&
                        g_nnOutput[0] >= 0.0f &&
                        g_nnOutput[0] <= 0.95f);

                DINT;
                // 推理期间若同方向已有更新的采样，不发布过期结果；
                // 新请求保留，下一轮处理；调用失败或输出越界时保留旧有效缓存。
                if (nnOK && (AnnUpdateReq & nnRequest) == 0U)
                {
                    if (nnDirection == 0U)
                    {
                        AnnDutyRise = g_nnOutput[0];
                        AnnDutyRiseValid = 1U;
                    }
                    else
                    {
                        AnnDutyFall = g_nnOutput[0];
                        AnnDutyFallValid = 1U;
                    }
                }
                if (AnnDutyRiseValid != 0U && AnnDutyFallValid != 0U)
                {
                    // 表示两方向都有有效缓存，不表示停止推理。
                    // 不清除待处理请求，后续每次边沿继续刷新对应缓存。
                    AnnInferenceReady = 1U;
                }
                EINT;
            }
            /* Tracking requests take priority over each bounded background step. */
            if (AnnUpdateReq == 0U) CapBackground_Task(AnnStartEnable);
            if (AnnUpdateReq == 0U) PortalSCI_poll();
        }
    }

    __interrupt void INT_myADC0_1_ISR(void)
    {

        //声明采样边沿判断变量
        float ioNow, ioStep; uint16_t useANN;
        uint16_t annEdgeDetected = 0U;
        uint16_t annEdgeDirection = 0U;
        uint16_t annEdgeRequest;
        uint32_t adcIsrStart;

        adcIsrStart = CPUTimer_getTimerCount(CPUTIMER0_BASE);




        i++;
        // 1. 读采样结果
        ADC_Sample_Raw.Vc1 = ADC_readResult(ADCBRESULT_BASE, ADC_SOC_NUMBER0);   // 电压1  Vc1其实是Vs1
        ADC_Sample_Raw.Vc2 = ADC_readResult(ADCARESULT_BASE, ADC_SOC_NUMBER0);   // 电压2  Vc2其实是Vs2
        ADC_Sample_Raw.Vin = ADC_readResult(ADCBRESULT_BASE, ADC_SOC_NUMBER1);   // 电压3
        ADC_Sample_Raw.iL1 = ADC_readResult(ADCBRESULT_BASE, ADC_SOC_NUMBER2);   // 电流1
        ADC_Sample_Raw.iL2 = ADC_readResult(ADCARESULT_BASE, ADC_SOC_NUMBER1);   // 电流2
        ADC_Sample_Raw.io  = ADC_readResult(ADCARESULT_BASE, ADC_SOC_NUMBER2);   // 电流3
        ADC_Sample_Raw.Vs1 = ADC_readResult(ADCBRESULT_BASE, ADC_SOC_NUMBER3);   // Vc1
        ADC_Sample_Raw.Vs2 = ADC_readResult(ADCARESULT_BASE, ADC_SOC_NUMBER3);   // Vc2
        ADC_Sample_Raw.ic  = ADC_readResult(ADCARESULT_BASE, ADC_SOC_NUMBER4);   // ic

        ADC_Sample_RealValue.Vc1 = ((float)ADC_Sample_Raw.Vc1 - ADC_Sample_Offset.Vc1) * ADC_Sample_Gain.Vc1;
        ADC_Sample_RealValue.Vc2 = ((float)ADC_Sample_Raw.Vc2 - ADC_Sample_Offset.Vc2) * ADC_Sample_Gain.Vc2;
        ADC_Sample_RealValue.Vin = ((float)ADC_Sample_Raw.Vin - ADC_Sample_Offset.Vin) * ADC_Sample_Gain.Vin;
        ADC_Sample_RealValue.iL1 = - ((float)ADC_Sample_Raw.iL1 - ADC_Sample_Offset.iL1) * ADC_Sample_Gain.iL1; //负号是因为采样芯片画反了
        ADC_Sample_RealValue.iL2 = - ((float)ADC_Sample_Raw.iL2 - ADC_Sample_Offset.iL2) * ADC_Sample_Gain.iL2; //负号是因为采样芯片画反了
        ADC_Sample_RealValue.io  = ((float)ADC_Sample_Raw.io  - ADC_Sample_Offset.io ) * ADC_Sample_Gain.io;
        ADC_Sample_RealValue.Vs1 = ((float)ADC_Sample_Raw.Vs1 - ADC_Sample_Offset.Vs1) * ADC_Sample_Gain.Vs1;
        ADC_Sample_RealValue.Vs2 = ((float)ADC_Sample_Raw.Vs2 - ADC_Sample_Offset.Vs2) * ADC_Sample_Gain.Vs2;
        ADC_Sample_RealValue.ic  = ((float)ADC_Sample_Raw.ic - ADC_Sample_Offset.ic) * ADC_Sample_Gain.ic;


        ADC_Sample_Filtered.Vc1 += LPF_ALPHA_10Hz * (ADC_Sample_RealValue.Vc1 - ADC_Sample_Filtered.Vc1);
        ADC_Sample_Filtered.Vc2 += LPF_ALPHA_10Hz * (ADC_Sample_RealValue.Vc2 - ADC_Sample_Filtered.Vc2);
        ADC_Sample_Filtered.Vin += LPF_ALPHA_10Hz * (ADC_Sample_RealValue.Vin - ADC_Sample_Filtered.Vin);
        ADC_Sample_Filtered.iL1 += LPF_ALPHA_10kHz * (ADC_Sample_RealValue.iL1 - ADC_Sample_Filtered.iL1);
        ADC_Sample_Filtered.iL2 += LPF_ALPHA_10kHz * (ADC_Sample_RealValue.iL2 - ADC_Sample_Filtered.iL2);
        ADC_Sample_Filtered.io  += LPF_ALPHA_10kHz * (ADC_Sample_RealValue.io  - ADC_Sample_Filtered.io);
        ADC_Sample_Filtered.Ioav += LPF_ALPHA_10Hz * (ADC_Sample_RealValue.io  - ADC_Sample_Filtered.Ioav);
        ADC_Sample_Filtered.Vs1 += LPF_ALPHA_10Hz * (ADC_Sample_RealValue.Vs1 - ADC_Sample_Filtered.Vs1);//Vc1
        ADC_Sample_Filtered.Vs2 += LPF_ALPHA_10Hz * (ADC_Sample_RealValue.Vs2 - ADC_Sample_Filtered.Vs2);//Vc2
        ADC_Sample_Filtered.ic  += LPF_ALPHA_10kHz * (ADC_Sample_RealValue.ic  - ADC_Sample_Filtered.ic);

        // 仅作为ANN启动门槛；置位后不因电压回落清零。
        if (AnnStartEnable == 0U && ADC_Sample_Filtered.Vin >= 265.0f)
            AnnStartEnable = 1U;

        // 推理改由实际电流边沿触发，不再由Vin变化触发。

        //边沿检测部分
        //启动先给一个wait状态，防止从脉冲边沿出启动，确保脉冲监测从low启动
        ioNow = ADC_Sample_RealValue.io;
        if (IoDetectorInitialized == 0U)
{
    LastIo = ioNow;
    ioStep = 0.0f;

    if (ioNow <= IO_LOW_A + IO_NEAR_ZERO_A)
        IoPhase = PHASE_LOW;
    else if (ioNow >= Io_Peak - IO_NEAR_PEAK_A)
        IoPhase = PHASE_HIGH;
    else
        IoPhase = PHASE_WAIT;

    IoDetectorInitialized = 1U;
}
else
{
    ioStep = ioNow - LastIo;
    LastIo = ioNow;
}


        switch (IoPhase)
    {
    case PHASE_LOW:
        // 先检测边沿，避免一次采样直接跳至18 A时漏掉上升沿。
        if (ioNow > IO_LOW_A + IO_NEAR_ZERO_A &&
            ioStep > IO_CHANGE_THRESHOLD)
        {
        AnnRiseTriggerIo = ioNow;
        AnnRiseTriggerStep = ioStep;
        AnnRisePreviousIo = ioNow - ioStep;

            annEdgeDetected = 1U;
            annEdgeDirection = 0U;
            IoPhase = PHASE_RISE;
            EdgeTicks = 0U;
            if (ioNow >= Io_Peak - IO_NEAR_PEAK_A)
            {
                IoPhase = PHASE_HIGH;
            }
        }
        else if (ioNow >= Io_Peak - IO_NEAR_PEAK_A)
            IoPhase = PHASE_HIGH;
        break;

    case PHASE_RISE:
    if (ioNow >= Io_Peak - IO_NEAR_PEAK_A)
    {
        IoPhase = PHASE_HIGH;
    }
    else if (EdgeTicks >= EDGE_MAX_TICKS ||
             ioStep < -IO_CHANGE_THRESHOLD)
    {
        IoPhase = PHASE_WAIT;
    }
    break;


    case PHASE_HIGH:
        // 先检测边沿，避免一次采样直接降至1 A时漏掉下降沿。
        if (ioNow < Io_Peak - IO_NEAR_PEAK_A &&
            ioStep < -IO_CHANGE_THRESHOLD)
        {
            annEdgeDetected = 1U;
            annEdgeDirection = 1U;
            IoPhase = PHASE_FALL;
            EdgeTicks = 0U;
            if (ioNow <= IO_LOW_A + IO_NEAR_ZERO_A)
            {
                IoPhase = PHASE_LOW;
            }
        }
        else if (ioNow <= IO_LOW_A + IO_NEAR_ZERO_A)
            IoPhase = PHASE_LOW;
        break;

    case PHASE_FALL:
    if (ioNow <= IO_LOW_A + IO_NEAR_ZERO_A)
    {
        IoPhase = PHASE_LOW;
    }
    else if (EdgeTicks >= EDGE_MAX_TICKS ||
             ioStep > IO_CHANGE_THRESHOLD)
    {
        IoPhase = PHASE_WAIT;
    }
    break;

    case PHASE_WAIT:
    if (ioNow <= IO_LOW_A + IO_NEAR_ZERO_A)
        IoPhase = PHASE_LOW;
    else if (ioNow >= Io_Peak - IO_NEAR_PEAK_A)
        IoPhase = PHASE_HIGH;
    break;

    }

    if (annEdgeDetected != 0U)
    {
        AnnRiseDetectCount++;
        AnnDirection = annEdgeDirection;
        // 每次新边沿重新锁存对应缓存；新边沿优先，按对应方向重新开始保持计数。
        EdgeDuty = (annEdgeDirection == 0U) ? AnnDutyRise : AnnDutyFall;
        if (annEdgeDirection == 0U)
            EdgeDuty -= AnnRiseDutyCorrection;
        else
            EdgeDuty -= AnnFallDutyCorrection;
        EdgeUseAnn = (AnnInferenceReady != 0U &&
                      EdgeDuty >= 0.0f && EdgeDuty <= 0.95f);
        AnnHoldRemaining = EdgeUseAnn ?
                   ((annEdgeDirection == 0U) ?
                    ANN_RISE_HOLD_CYCLES : ANN_FALL_HOLD_CYCLES) : 0U;

        // 只重置本方向有效长度，不循环清空数组；无接管时长度保持0。
        if (annEdgeDirection == 0U)
            AnnRisePiTraceCount = 0U;
        else
            AnnFallPiTraceCount = 0U;

        // 此处尚未执行本次PI计算，Output仍是上一拍已限幅的PI结果。
        // 每次检测到边沿均保存，不依赖ANN是否就绪或实际接管。
        AnnPiOutputBeforeReplace = CurrCon_Reg.Output;
        AnnPiOutputAtEdge[annEdgeDirection] = CurrCon_Reg.Output;

        if (AnnStartEnable != 0U)
        {
            // 每次边沿都锁存新输入并触发推理，不清除已有缓存有效标志。
            // 同方向未处理请求保留最新采样；两方向请求独立保留。
            annEdgeRequest = (annEdgeDirection == 0U) ? ANN_RISE_REQ : ANN_FALL_REQ;
            AnnCapturedPeak[annEdgeDirection] = Io_Peak;
            AnnCapturedTime[annEdgeDirection] = Rise_time;
            // 原通道映射不变：结构体Vs1是实际Vc1，Vc1是实际Vs1/VG1。
            AnnCapturedVc1[annEdgeDirection] = ADC_Sample_Filtered.Vs1;
            AnnCapturedVs1[annEdgeDirection] = ADC_Sample_Filtered.Vc1;
            AnnUpdateReq |= annEdgeRequest;
        }
    }

    if (IoPhase == PHASE_RISE || IoPhase == PHASE_FALL)
     {   EdgeTicks++;
     AnnRiseWriteCount++;
     }
    if (AnnInferenceReady == 0U)
    {
        EdgeUseAnn = 0U;
        AnnHoldRemaining = 0U;
    }

    // 保持原PI分支始终计算；ANN仅在下方CMP写入前选择。
    useANN= 0U;


        // 2. PI 控制
        VoltCon_Reg.Ref = 0.75f * ADC_Sample_Filtered.Vin;
        VoltCon_Reg.Err_Old = VoltCon_Reg.Err_New;
        VoltCon_Reg.Err_New = VoltCon_Reg.Ref - ADC_Sample_Filtered.Vc1;    
        //电压环PI与ANN选择
        if(useANN == 0U)
        {
        VoltCon_Reg.Output = VoltCon_Reg.Output + (VoltCon_Reg.Kp + VoltCon_Reg.Ki) * VoltCon_Reg.Err_New - VoltCon_Reg.Kp * VoltCon_Reg.Err_Old;
        }

        if (VoltCon_Reg.Output >= 0.8f)
        {
                VoltCon_Reg.Output = 0.8f;
        }
        if (VoltCon_Reg.Output <= -0.8f)
        {
                VoltCon_Reg.Output = -0.8f;
        }

        CurrCon_Reg.Ref = 0.5f * (ADC_Sample_Filtered.io - ADC_Sample_Filtered.Ioav) - VoltCon_Reg.Output;
        CurrCon_Reg.Err_Old = CurrCon_Reg.Err_New;
        CurrCon_Reg.Err_New =  CurrCon_Reg.Ref - ADC_Sample_Filtered.iL1;

        //电流环PI与ANN选择
        if (useANN != 0U) 
        {
            CurrCon_Reg.Output = EdgeDuty;
        }
        else 
        {
        CurrCon_Reg.Output = CurrCon_Reg.Output + (CurrCon_Reg.Kp + CurrCon_Reg.Ki) * CurrCon_Reg.Err_New - CurrCon_Reg.Kp * CurrCon_Reg.Err_Old;    
        }


        if (CurrCon_Reg.Output >= 0.95f)
        {
                CurrCon_Reg.Output = 0.95f;
        }
        if (CurrCon_Reg.Output <= 0.00f)
        {
                CurrCon_Reg.Output = 0.00f;
        }

        // PI段保持原样且每次中断继续计算。仅在写入CMP前选择ANN缓存。
        uint16_t CMPA = (uint16_t)(CurrCon_Reg.Output * EPWM_TIMER_TBPRD + 0.5f);
        AnnPwmActive = 0U;
        if (0U && AnnInferenceReady != 0U && EdgeUseAnn != 0U &&
            AnnHoldRemaining != 0U &&
            EdgeDuty >= 0.0f && EdgeDuty <= 0.95f)
        {
            // 使用边沿触发时锁存的同一占空比，不因平台或后台新结果提前结束。
            CMPA = (uint16_t)(EdgeDuty * EPWM_TIMER_TBPRD + 0.5f);
            AnnPwmActive = 1U;
            count++;
        }


        EPWM_setCounterCompareValue(EPWM1_BASE,EPWM_COUNTER_COMPARE_A,CMPA);
        EPWM_setCounterCompareValue(EPWM2_BASE,EPWM_COUNTER_COMPARE_A,CMPA);
        // 在两路CMP写入之后记录，保持原来的PWM选择与写入顺序。
        // 保存本拍已计算、已限幅的PI输出，不是ANN占空比或前一拍快照。
        if (AnnPwmActive != 0U)
        {
            if (AnnDirection == 0U)
            {
                if (AnnRisePiTraceCount < ANN_PI_TRACE_LEN)
                {
                    AnnRisePiTrace[AnnRisePiTraceCount] = CurrCon_Reg.Output;
                    AnnRisePiTraceCount++;
                }
            }
            else if (AnnDirection == 1U)
            {
                if (AnnFallPiTraceCount < ANN_PI_TRACE_LEN)
                {
                    AnnFallPiTrace[AnnFallPiTraceCount] = CurrCon_Reg.Output;
                    AnnFallPiTraceCount++;
                }
            }
        }


        // 本次CMP及PI记录已完成，消耗一次保持次数。
        if (AnnHoldRemaining != 0U)
        {
            AnnHoldRemaining--;
            if (AnnHoldRemaining == 0U)
                EdgeUseAnn = 0U;
        }

        // 3. 清中断标志
        CapMonitor_OnAdc((uint16_t)ADC_Sample_Raw.Vs1,
                         (uint16_t)ADC_Sample_Raw.ic,
                         (uint16_t)ADC_Sample_Raw.io, adcIsrStart,
                         ADC_getInterruptOverflowStatus(ADCA_BASE, ADC_INT_NUMBER1));
        if (ADC_getInterruptOverflowStatus(ADCA_BASE, ADC_INT_NUMBER1))
            ADC_clearInterruptOverflowStatus(ADCA_BASE, ADC_INT_NUMBER1);
        ADC_clearInterruptStatus(ADCA_BASE, ADC_INT_NUMBER1);
        Interrupt_clearACKGroup(INTERRUPT_ACK_GROUP1);

        adcIsrStart = (uint32_t)(adcIsrStart -
                     CPUTimer_getTimerCount(CPUTIMER0_BASE));
        if (g_nnTimerClockHz != 0UL)
            g_adcIsrTimeUs = (float)adcIsrStart *
                            (1000000.0f / (float)g_nnTimerClockHz);
        else
            g_adcIsrTimeUs = -1.0f;
        if (g_adcIsrTimeUs > g_capAdcIsrMaxUs)
            g_capAdcIsrMaxUs = g_adcIsrTimeUs;

    }

    void Controller_Init(void)
    {
        VoltCon_Reg.Kp          = 0.025f;
        VoltCon_Reg.Ki          = 0.000001f;
        VoltCon_Reg.Err_New     = 0.0f;
        VoltCon_Reg.Err_Old     = 0.0f;
        VoltCon_Reg.Ref         = 0.0f;
        VoltCon_Reg.Output      = 0.0f;

        CurrCon_Reg.Kp          = 0.0045f;//0.0045
        CurrCon_Reg.Ki          = 0.0008f; //0.0008
        CurrCon_Reg.Err_New     = 0.0f;
        CurrCon_Reg.Err_Old     = 0.0f;
        CurrCon_Reg.Ref         = 0.0f;
        CurrCon_Reg.Output      = 0.0f;
    }

    void ADC_Sample_Init(void)
    {
        ADC_Sample_Raw.Vc1 = 0.0;   
        ADC_Sample_Raw.Vc2 = 0.0;   
        ADC_Sample_Raw.Vin = 0.0;  
        ADC_Sample_Raw.iL1 = 0.0;   
        ADC_Sample_Raw.iL2 = 0.0;   
        ADC_Sample_Raw.io  = 0.0;   
        ADC_Sample_Raw.Vs1 = 0.0;   
        ADC_Sample_Raw.Vs2 = 0.0;
        ADC_Sample_Raw.ic  = 0.0;   


        ADC_Sample_Offset.Vc1 = 0.0;   
        ADC_Sample_Offset.Vc2 = 0.0;   
        ADC_Sample_Offset.Vin = 0.0;  
        ADC_Sample_Offset.iL1 = 1983;   
        ADC_Sample_Offset.iL2 = 1987;   
        ADC_Sample_Offset.io  = 0;   
        ADC_Sample_Offset.Vs1 = 0.0;   
        ADC_Sample_Offset.Vs2 = 0.0;
        ADC_Sample_Offset.ic  = 1467;   

        ADC_Sample_Gain.Vc1 = 0.12599f;   
        ADC_Sample_Gain.Vc2 = 0.12662f;   
        ADC_Sample_Gain.Vin = 0.12599f;  
        ADC_Sample_Gain.iL1 = 0.02517f;   
        ADC_Sample_Gain.iL2 = 0.02517f;   
        ADC_Sample_Gain.io  = 0.00724f;    
        ADC_Sample_Gain.Vs1 = 0.12599f;   
        ADC_Sample_Gain.Vs2 = 0.12599f;
        ADC_Sample_Gain.ic  = 0.022911f;   //0001:0.022911  其他:0.027493  

    }


    //
    // End of File
    //
