/* 增量式PI状态：Kp/Ki为离散控制系数；Ref为参考，Err_New/Old为本拍/上拍误差。 */
struct CON_REG
{
    float   Kp;
    float   Ki;
    float   Ref;
    float   Err_New;
    float   Err_Old;
    float   Output;
};

/* 共用采样结构：Raw/Offset使用ADC码，Gain为物理量/码，RealValue/Filtered使用V或A。
 * Vc1/Vc2字段对应硬件Vs1/Vs2；Vs1/Vs2字段对应硬件Vc1/Vc2，扩展时需保持通道一致。 */
struct ADC_Sample_Reg
{
    float    Vc1;
    float    Vc2;
    float    Vin;
    float    iL1;
    float    iL2;
    float    io;
    float    Ioav;
    float    Vs1;              
    float    Vs2;              
    float    ic;
};

/* 这些对象在main.c定义，实时中断更新；后台模块读取快照时需遵守各自接口约定。 */
extern struct ADC_Sample_Reg ADC_Sample_Raw;
extern struct ADC_Sample_Reg ADC_Sample_Offset;
extern struct ADC_Sample_Reg ADC_Sample_Gain;
extern struct ADC_Sample_Reg ADC_Sample_RealValue;
extern struct ADC_Sample_Reg ADC_Sample_Filtered;

extern struct   CON_REG VoltCon_Reg;
extern struct   CON_REG CurrCon_Reg;

/* 启动时初始化控制器和采样标定；HFC_Control仅为接口声明，调用关系以实现为准。 */
extern void     Controller_Init(void);
extern void     HFC_Control(void);
extern void     ADC_Sample_Init(void);
  
