struct CON_REG
{
    float   Kp;
    float   Ki;
    float   Ref;
    float   Err_New;
    float   Err_Old;
    float   Output;
};

struct ADC_Sample_Reg
{
    float    Vc1;
    float    Vc2;
    float    Vin;
    float    iL1;
    float    iL2;
    float    io;
    float    Ioav;
    float    Vs1;//其实是Vc1
    float    Vs2;//其实是Vc2
    float    ic;
};

extern struct ADC_Sample_Reg ADC_Sample_Raw;
extern struct ADC_Sample_Reg ADC_Sample_Offset;
extern struct ADC_Sample_Reg ADC_Sample_Gain;
extern struct ADC_Sample_Reg ADC_Sample_RealValue;
extern struct ADC_Sample_Reg ADC_Sample_Filtered;

extern struct   CON_REG VoltCon_Reg;
extern struct   CON_REG CurrCon_Reg;

extern void     Controller_Init(void);
extern void     HFC_Control(void);
extern void     ADC_Sample_Init(void);
//
