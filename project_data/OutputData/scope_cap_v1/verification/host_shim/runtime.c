int _fltused=0;
static float (*log_callback)(float);
void SetLogCallback(float (*fn)(float)) { log_callback=fn; }
float test_log1pf(float x) { return log_callback(x); }
void *memset(void *p,int v,unsigned long long n) {volatile unsigned char *q=(volatile unsigned char*)p; while(n--) *q++=(unsigned char)v; return p; }
