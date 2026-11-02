                                                                        
                                                                                          
                                                                            
                                                                        
   
/* SCI前台协议：命令接收、监测快照和分步发送。无串口中断或动态内存。
 * 单次轮询最多处理4个接收字节、4个CRC字节及4个发送字节，ADC可抢占前台。 */
#include "driverlib.h"
#include "device.h"
#include "main.h"
#include "nn_cpu_test.h"
#include "portal_sci.h"
#include "cap_monitor.h"
#include "cap_sci.h"
#include <string.h>

extern volatile uint16_t AnnUpdateReq, AnnInferenceReady, AnnPwmActive, AnnStartEnable;
extern volatile float Io_Peak, Rise_time, g_adcIsrTimeUs, g_annTimeUs;

volatile uint32_t g_portalPollMaxCycles = 0UL;
volatile uint32_t g_portalRxErrors = 0UL;
volatile uint32_t g_portalBadCommands = 0UL;
volatile uint32_t g_portalFramesSent = 0UL;
volatile uint16_t g_portalReady = 0U;
volatile uint32_t g_portalCapEvents = 0UL, g_portalCapFramesSent = 0UL;
volatile uint32_t g_portalCapStatusFramesSent = 0UL, g_portalCapDropped = 0UL;

/* 固定缓冲区：TX为256字符，RX为64字符；接收半帧超时为计时频率的1/10。 */
#define BASE SCIA_BASE
#define TX_SIZE 256U
#define RX_SIZE 64U
#define RX_TIMEOUT_DIV 10UL
static char tx[TX_SIZE], rx[RX_SIZE];
static uint16_t txLen, txPos, stage, crcPos, rxLen, rxDrop;
static uint16_t crc, rxCrc, rxAfterStar, rxChecksum, rxHexCount, rxInFrame;
/* 默认连续监测10Hz；RATE命令可选择1、2、5、10、20Hz，SNAP触发一次快照。 */
static uint16_t streamEnabled = 1U, rate = 10U, snapPending;
static uint16_t ackPending;
static uint32_t ackSequence;
static const char *ackCommand;
static const char *ackResult;
static float values[16];
                                                                               
static float capMonitorValues[13];
static uint16_t capMonitorDue;
static uint32_t capMonitorSequence, capMonitorSampleSequence, capMonitorMs;
static uint32_t sequence, sampleSequence, sampleMs, millis, tickRemainder;
static uint32_t lastTimer, periodTicks, rxLastTimer, lastFrameTimer;
static uint16_t initialized;
enum { PACKET_TRK=1U, PACKET_ACK=2U, PACKET_CAPSTAT=3U, PACKET_CAP=4U };
static uint16_t packetKind, capPendingReady, capFollowup;
static uint32_t capMs;
/* 一份待发和一份正在发送的辨识事件；替换尚未开始发送的待发事件时累计丢弃数。 */
static PortalCapSnapshot capPending, capActive;

/* CRC16逐字节更新，多项式0x1021，帧起始值0xFFFF；校验范围不含@、*及尾随换行。 */
static uint16_t crcByte(uint16_t c, uint16_t b)
{
    uint16_t n;
    c ^= (uint16_t)(b << 8);
    for(n=0U;n<8U;n++)
        c = (c & 0x8000U) ? (uint16_t)((c << 1) ^ 0x1021U) : (uint16_t)(c << 1);
    return c;
}
static void put(char c) { if(txLen < TX_SIZE-8U) tx[txLen++] = c; }
static void literal(const char *p) { while(*p != '\0') put(*p++); }
static void integer(uint32_t n)
{
    char digits[10]; uint16_t count=0U;
    do { digits[count++] = (char)('0' + (n % 10UL)); n /= 10UL; } while(n != 0UL && count<10U);
    while(count != 0U) put(digits[--count]);
}
/* 监测浮点格式化：根据字段输出3或6位小数；无效值编码为空字段，不伪造零值。 */
static void fixed(float value, uint16_t decimals)
{
    uint32_t scale = decimals == 6U ? 1000000UL : 1000UL;
    uint32_t n, fraction, divisor;
                                                                     
    if(!(value >= -1000.0f && value <= 1000.0f)) return;
    if(value < 0.0f) { put('-'); value = -value; }
    n=(uint32_t)(value*(float)scale+0.5f);
    integer(n/scale); put('.'); fraction=n%scale;
    for(divisor=scale/10UL;divisor != 0UL;divisor/=10UL)
        put((char)('0'+(fraction/divisor)%10UL));
}
static void capFixed(float value)
{
    uint32_t n,f,divisor;
    if(!(value>=0.0f && value<=10000.0f))return;
    n=(uint32_t)(value*1000.0f+0.5f);integer(n/1000UL);put('.');f=n%1000UL;
    for(divisor=100UL;divisor!=0UL;divisor/=10UL)put((char)('0'+(f/divisor)%10UL));
}
static void nonnegativeInteger(float value)
{
    if(value>=0.0f && value<=4000000000.0f)integer((uint32_t)(value+0.5f));
}
static void beginPacket(void) { txLen=0U;txPos=0U;crcPos=1U;crc=0xFFFFU;put('@'); }
/* TRK快照来自前台读取，不保证所有字段对应同一ADC中断。
 * TRK电压映射为Vs1字段→物理VC1，Vc1字段→VG1；时间输入换算为us。 */
static void beginTelemetry(uint32_t now)
{
    volatile struct ADC_Sample_Reg *sample = &ADC_Sample_Filtered;
    beginPacket(); literal("TRK,1,");
    sampleSequence=sequence++; sampleMs=millis;
                                                                          
                                                                      
                                                                
       
    values[0]=Io_Peak; values[1]=Rise_time*1000000.0f;
    values[2]=sample->Vs1; values[3]=sample->Vc1; values[4]=sample->Vin;
    values[5]=sample->io; values[6]=sample->iL1; values[7]=sample->iL2; values[8]=sample->ic;
    values[9]=(g_nnReturnCode == 0 && g_nnTestState == 2U) ? g_nnOutput[0] : -1001.0f;
    values[10]=((volatile struct CON_REG *)&CurrCon_Reg)->Output;
    values[11]=g_nnInput[4]; values[12]=(float)AnnInferenceReady; values[13]=(float)AnnPwmActive;
    values[14]=g_adcIsrTimeUs; values[15]=g_annTimeUs;
    stage=1U; lastFrameTimer=now; snapPending=0U;
    packetKind=PACKET_TRK;
    capMonitorDue=1U;
}
/* CAPMON独立监测快照：首个电压字段直接取Filtered.Vc1，
 * 其后为ic、io、各阶段耗时、采样数、采样率、状态、有效位、故障及告警。 */
static void beginCapMonitor(void)
{
    volatile struct ADC_Sample_Reg *sample=&ADC_Sample_Filtered;
    beginPacket();literal("CAPMON,1,");
    capMonitorSampleSequence=capMonitorSequence++;capMonitorMs=millis;
                                                                  
                                                                       
    capMonitorValues[0]=sample->Vc1;
    capMonitorValues[1]=sample->ic;
    capMonitorValues[2]=sample->io;
    capMonitorValues[3]=g_adcIsrTimeUs;
    capMonitorValues[4]=g_capInferUs;
    capMonitorValues[5]=g_capPreprocessUs;
    capMonitorValues[6]=g_capLatencyUs;
    capMonitorValues[7]=(float)g_capSampleCount;
    capMonitorValues[8]=g_capSampleRateHz;
    capMonitorValues[9]=(float)g_capState;
    capMonitorValues[10]=(float)g_capValid;
    capMonitorValues[11]=(float)g_capFault;
    capMonitorValues[12]=(float)g_capWarning;
    capMonitorDue=0U;packetKind=5U;stage=70U;
}
/* 前台提交辨识事件并复核结果有效性；CAPSTAT报告事件状态，只有有效事件跟随CAP结果。 */
void PortalSCI_publishCap(const PortalCapSnapshot *snapshot)
{
    uint16_t i;
    if(!initialized){g_portalCapDropped++;return;}
    if(capPendingReady)g_portalCapDropped++;
    capPending=*snapshot;
                                                                              
    if(capPending.state!=CAP_DONE || capPending.fault!=0U)capPending.valid=0U;
    for(i=0U;i<4U;i++)
        if(!(capPending.capacitance_uF[i]>0.0f && capPending.capacitance_uF[i]<=10000.0f &&
             capPending.esr_mOhm[i]>0.0f && capPending.esr_mOhm[i]<=10000.0f))capPending.valid=0U;
    capPendingReady=1U;g_portalCapEvents++;
}
static void beginCapStatus(void)
{
    capActive=capPending;capPendingReady=0U;capMs=millis;
    beginPacket();literal("CAPSTAT,1,");packetKind=PACKET_CAPSTAT;stage=30U;
    capFollowup=capActive.valid;
}
static void beginCapResult(void)
{
    beginPacket();literal("CAP,1,");packetKind=PACKET_CAP;stage=50U;capFollowup=0U;
}
static void resetRx(void)
{
    rxLen=0U;rxDrop=0U;rxCrc=0xFFFFU;rxAfterStar=0U;rxChecksum=0U;rxHexCount=0U;rxInFrame=0U;
}
/* 命令帧为@CMD,1,id,command[,arg]*CRC\n；验证CRC、字段数及32位命令编号。
 * 支持PING、SNAP、CAPTURE、CAPSTATUS、STREAM和RATE；CAPTURE仅置采样请求。 */
static void command(void)
{
    char *token[6]; uint16_t n=1U,k; uint32_t id=0UL; const char *result="OK";
    if(rxDrop || !rxAfterStar || rxHexCount!=4U || rxChecksum!=rxCrc || rxLen<9U)
    { g_portalBadCommands++;return; }
    rx[rxLen]='\0'; token[0]=rx;
    for(k=0U;k<rxLen;k++) if(rx[k]==',') { rx[k]='\0';if(n<6U)token[n++]=&rx[k+1U];else {g_portalBadCommands++;return;} }
    if(n<4U || strcmp(token[0],"CMD")!=0 || strcmp(token[1],"1")!=0 || ackPending)
    {g_portalBadCommands++;return;}
    if(token[2][0]=='\0') {g_portalBadCommands++;return;}
    for(k=0U;token[2][k]!='\0';k++)
    {
        if(k>=10U || token[2][k]<'0' || token[2][k]>'9' || id>429496729UL ||
          (id==429496729UL && token[2][k]>'5')) {g_portalBadCommands++;return;}
        id=id*10UL+(uint32_t)(token[2][k]-'0');
    }
    ackCommand="UNKNOWN";
    if(n==4U && strcmp(token[3],"PING")==0) ackCommand="PING";
    else if(n==4U && strcmp(token[3],"SNAP")==0) {ackCommand="SNAP";snapPending=1U;}
    else if(n==4U && strcmp(token[3],"CAPTURE")==0)
    {
        ackCommand="CAPTURE";
        if(!g_capEnable)result="DISABLED";
        else if(!AnnStartEnable)result="NOT_READY";
        else if(g_capRequest || (g_capState>=CAP_CAPTURE && g_capState<=CAP_CNN))result="BUSY";
        else g_capRequest=1U;                                                   
    }
    else if(n==4U && strcmp(token[3],"CAPSTATUS")==0)
    {ackCommand="CAPSTATUS";CapSCI_Publish();}
    else if(n==5U && strcmp(token[3],"STREAM")==0 &&
           (strcmp(token[4],"0")==0 || strcmp(token[4],"1")==0))
    {ackCommand="STREAM";streamEnabled=(uint16_t)(token[4][0]-'0');}
    else if(n==5U && strcmp(token[3],"RATE")==0)
    {
        ackCommand="RATE";k=0U;
        if(strcmp(token[4],"1")==0)k=1U;else if(strcmp(token[4],"2")==0)k=2U;
        else if(strcmp(token[4],"5")==0)k=5U;else if(strcmp(token[4],"10")==0)k=10U;
        else if(strcmp(token[4],"20")==0)k=20U;
        if(k) {rate=k;periodTicks=g_nnTimerClockHz/(uint32_t)rate;} else result="ERR";
    }
    else result="ERR";
    ackSequence=id;ackResult=result;ackPending=1U;
}
/* 每次接收最多4字节；@重新同步，*后接4位十六进制CRC，换行结束帧。
 * 接收错误复位SCI接收状态；超时、超长或非法字符使本帧失效。 */
static void receive(uint32_t now)
{
    uint16_t budget=4U,c,h;
    if((SCI_getRxStatus(BASE)&SCI_RXSTATUS_ERROR)!=0U || SCI_getOverflowStatus(BASE))
    {
        SCI_performSoftwareReset(BASE); SCI_resetRxFIFO(BASE); SCI_clearOverflowStatus(BASE);
        resetRx();g_portalRxErrors++;
    }
    if(rxLen && (uint32_t)(rxLastTimer-now)>g_nnTimerClockHz/RX_TIMEOUT_DIV)resetRx();
    while(budget-- && SCI_getRxFIFOStatus(BASE)!=SCI_FIFO_RX0)
    {
        c=SCI_readCharNonBlocking(BASE)&0xFFU;rxLastTimer=now;
        if(c=='@') {resetRx();rxInFrame=1U;continue;}
        if(!rxInFrame)continue;
        if(c=='\n') {command();resetRx();continue;}
        if(c=='\r')continue;
        if(rxDrop)continue;
        if(c=='*') {if(rxAfterStar)rxDrop=1U;rxAfterStar=1U;continue;}
        if(rxAfterStar)
        {
            if(c>='0'&&c<='9')h=c-'0';else if(c>='A'&&c<='F')h=c-'A'+10U;
            else if(c>='a'&&c<='f')h=c-'a'+10U;else {rxDrop=1U;continue;}
            if(rxHexCount>=4U){rxDrop=1U;continue;}
            rxChecksum=(uint16_t)((rxChecksum<<4)|h);rxHexCount++;
        }
        else if(rxLen<RX_SIZE-1U && c>=32U && c<=126U)
        {rx[rxLen++]=(char)c;rxCrc=crcByte(rxCrc,c);}
        else rxDrop=1U;
    }
}
/* 按时钟与115200波特率初始化SCIA/FIFO，并建立毫秒与监测周期计时状态。
 * 选择板载调试器串口时由宏控制RX复用，需与实际接线一致。 */
void PortalSCI_init(void)
{
    uint32_t lspclk=SysCtl_getLowSpeedClock(DEVICE_OSCSRC_FREQ);
    initialized=0U;g_portalReady=0U;
    if(g_nnTimerClockHz==0UL || lspclk==0UL)return;
#if PORTAL_SCI_XDS110
                                                                         
                                                                            
       
    GPIO_setPinConfig(GPIO_25_GPIO25);
    GPIO_setDirectionMode(25U,GPIO_DIR_MODE_IN);
    GPIO_setAnalogMode(28U,GPIO_ANALOG_DISABLED);
    GPIO_setPinConfig(GPIO_28_SCIA_RX);
    GPIO_setPadConfig(28U,GPIO_PIN_TYPE_PULLUP);
    GPIO_setQualificationMode(28U,GPIO_QUAL_ASYNC);
#endif
    SCI_disableModule(BASE);
    SCI_setConfig(BASE,lspclk,PORTAL_SCI_BAUD,SCI_CONFIG_WLEN_8|SCI_CONFIG_STOP_ONE|SCI_CONFIG_PAR_NONE);
    SCI_disableInterrupt(BASE,SCI_INT_RXFF|SCI_INT_TXFF|SCI_INT_FE|SCI_INT_OE|SCI_INT_PE|
                         SCI_INT_RXERR|SCI_INT_RXRDY_BRKDT|SCI_INT_TXRDY);
    SCI_resetTxFIFO(BASE);SCI_resetRxFIFO(BASE);SCI_clearOverflowStatus(BASE);
    SCI_enableFIFO(BASE);SCI_enableModule(BASE);SCI_performSoftwareReset(BASE);
    resetRx();stage=0U;txLen=0U;txPos=0U;
    lastTimer=CPUTimer_getTimerCount(CPUTIMER0_BASE);lastFrameTimer=lastTimer;
    periodTicks=g_nnTimerClockHz/(uint32_t)rate;
    capPendingReady=0U;capFollowup=0U;packetKind=0U;capMonitorDue=0U;capMonitorSequence=0UL;
    initialized=1U;g_portalReady=1U;
}
/* 有限工作量轮询：先接收命令，再按ACK→CAP结果续帧→CAP状态→CAPMON→TRK选择新帧。
 * stage分步格式化字段、累加CRC及发送；有推理请求时提前返回，将控制计算置于前台优先级。 */
void PortalSCI_poll(void)
{
    uint32_t now,start,elapsed,ticksPerMs;uint16_t budget,k;static const char hex[]="0123456789ABCDEF";
    if(!initialized || AnnUpdateReq!=0U)return;
    start=now=CPUTimer_getTimerCount(CPUTIMER0_BASE);
    ticksPerMs=g_nnTimerClockHz/1000UL;
    elapsed=(uint32_t)(lastTimer-now);lastTimer=now;
    tickRemainder+=elapsed;millis+=tickRemainder/ticksPerMs;tickRemainder%=ticksPerMs;
    receive(now);
    if(AnnUpdateReq!=0U)goto finish;
    if(stage==0U)
    {
        if(ackPending)
        {
            beginPacket();literal("ACK,1,");integer(ackSequence);put(',');literal(ackCommand);
            put(',');literal(ackResult);ackPending=0U;stage=20U;
            packetKind=PACKET_ACK;
        }
        else if(capFollowup)beginCapResult();
        else if(capPendingReady)beginCapStatus();
        else if(capMonitorDue)beginCapMonitor();
        else if(snapPending || (streamEnabled && (uint32_t)(lastFrameTimer-now)>=periodTicks))beginTelemetry(now);
    }
    else if(stage<19U)
    {
        if(stage==1U)integer(sampleSequence);
        else if(stage==2U)integer(sampleMs);
        else
        {
            k=stage-3U;
            if(k>=11U && k<=13U)integer((uint32_t)values[k]);
            else fixed(values[k],(k==9U || k==10U)?6U:3U);
        }
        if(stage<18U)put(',');stage++;
        if(stage==19U)stage=20U;
    }
    else if(stage>=30U && stage<=39U)
    {
        switch(stage) {
        case 30U:integer(capActive.sequence);break;
        case 31U:integer(capMs);break;
        case 32U:integer(capActive.state);break;
        case 33U:integer(capActive.valid);break;
        case 34U:integer(capActive.fault);break;
        case 35U:integer(capActive.warning);break;
        case 36U:integer(capActive.sample_count);break;
        case 37U:integer(capActive.error_count);break;
        case 38U:nonnegativeInteger(capActive.sample_rate_hz);break;
        default:nonnegativeInteger(capActive.latency_us);break;
        }
        if(stage<39U){put(',');stage++;}else stage=20U;
    }
    else if(stage>=50U && stage<=59U)
    {
        if(stage==50U)integer(capActive.sequence);
        else if(stage==51U)integer(capMs);
        else {
            k=stage-52U;
            capFixed((k&1U)?capActive.esr_mOhm[k/2U]:capActive.capacitance_uF[k/2U]);
        }
        if(stage<59U){put(',');stage++;}else stage=20U;
    }
    else if(stage>=70U && stage<=84U)
    {
        if(stage==70U)integer(capMonitorSampleSequence);
        else if(stage==71U)integer(capMonitorMs);
        else {
            k=stage-72U;
            if(k<=3U)fixed(capMonitorValues[k],3U);
            else nonnegativeInteger(capMonitorValues[k]);
        }
        if(stage<84U){put(',');stage++;}else stage=20U;
    }
    /* 帧内容格式化完成后分次计算CRC；附加*、4位CRC和换行，再进入发送阶段。 */
    else if(stage==20U)
    {
        budget=4U;
        while(budget-- && crcPos<txLen) {crc=crcByte(crc,(uint16_t)tx[crcPos]);crcPos++;}
        if(crcPos==txLen)
        {
            put('*');for(k=0U;k<4U;k++)put(hex[(crc>>(12U-4U*k))&15U]);put('\n');stage=21U;
        }
    }
    /* 仅在TX FIFO有空间时发送，单次最多4字节；整帧完成后更新对应计数。 */
    if(stage==21U)
    {
        budget=4U;
        while(budget-- && txPos<txLen && SCI_getTxFIFOStatus(BASE)!=SCI_FIFO_TX16)
        {
            if(packetKind==PACKET_CAP){g_capTxText[txPos]=tx[txPos];g_capTxText[txPos+1U]='\0';}
            SCI_writeCharNonBlocking(BASE,(uint16_t)tx[txPos++]&0xFFU);
        }
        if(txPos==txLen) {
            stage=0U;g_portalFramesSent++;
            if(packetKind==PACKET_CAP)g_portalCapFramesSent++;
            else if(packetKind==PACKET_CAPSTAT)g_portalCapStatusFramesSent++;
        }
    }
finish:
    elapsed=(uint32_t)(start-CPUTimer_getTimerCount(CPUTIMER0_BASE));
    if(elapsed>g_portalPollMaxCycles)g_portalPollMaxCycles=elapsed;
}
