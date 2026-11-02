# 脉冲平抑系统强瞬变电流高精度跟踪控制与电容阵列单体参数辨识

**AI + Power Electronics Platform**

仓库：https://github.com/zhouyingjie-111/AI-Power-Electronics-Platform

Electronic Capacitor Platform：集成 ANN 脉冲负载快速追踪、CNN 电容参数辨识、Simulink 仿真、串口交互和电力电子智能对话的本地研究平台。

项目自有源码采用 [MIT 许可证](LICENSE)。第三方代码、TI文件和项目素材的许可范围见 [第三方说明](THIRD_PARTY_NOTICES.md)。准备上传GitHub时，按 [发布教程](GITHUB发布教程.md) 操作。

当前维护版：Windows x64 / Python 3.10。包含快速追踪、电容参数辨识、模型训练/编译、Simulink 仿真、串口监测、智能对话及数据维护。

## 运行

1. 安装 Python 3.10 64位（包含 Python Launcher），VS Code 安装 Microsoft Python / Python Debugger 扩展。
2. 完整解压本目录，首次运行 `安装依赖.bat`。随包 wheels 可离线安装基础网站依赖。
3. 在 VS Code 打开整个文件夹或 `电子电容平台.code-workspace`，按 F5，选择“启动电子电容平台”。也可双击 `启动网页.bat`。
4. 浏览器自动打开。端口5001被占用时自动选择后续空闲端口，以终端输出为准。停止时在 VS Code 停止调试或在启动终端按 Ctrl+C。

命令行启动和检查：

```powershell
.venv\Scripts\python.exe run.py --check
.venv\Scripts\python.exe run.py
.venv\Scripts\python.exe -m unittest discover -s webapp/tests -p "test_*.py" -q
```

修改源码后停止并重新启动服务。静态资源保留在本地，无需 npm 或前端构建步骤。

## 使用

快速追踪、参数辨识分别提供背景、算法与数据、仿真验证和实验结果页面。展开对应区域操作；快速追踪仅显示推理耗时，已隐藏控制中断耗时。

- **训练**：选择默认数据或上传文件，查看日志，完成后下载模型；编译是独立步骤，按目标选择 CPU/NPU 再下载结果。
- **仿真**：选择模型并设置参数，查看进度、模型画面与示波器波形；电容辨识已有结果对比区域暂留空。
- **串口**：选择 XDS110 Application/User UART，通常使用115200、8N1。两个功能页共用连接，切页不重连。开始辨识发送 CAPTURE。电容监测 VC1 取配套固件的 ADC_Sample_Filtered.Vc1。
- **对话**：选择提供商、输入自己的API Key并选模型；支持附件、历史与文件下载。历史按当前 Windows 账户加密，密钥不写入发布配置。
- **数据维护**：仅维护 project_data/InputData 与 OutputData，删除前请自行备份数据。

## 工具配置

基础网页不要求安装 MATLAB、CCS 或训练框架。对应功能实际运行需要本机工具；网站提供接口并不等于工具已安装。

复制 config.example.json 为 config.json，按需填写下表。环境变量优先于本机配置；所有相对路径以项目根目录为准。配置格式和端口在启动时统一校验。

| 选项 | 用途 |
|---|---|
| HOST / PORT | 监听地址/首选端口，默认127.0.0.1/5001 |
| DELTA_PROJECT_ROOT | 数据工程目录，默认project_data，无需桌面原目录 |
| ANN_PYTHON / CAP_PYTHON | 运行ANN/CNN任务的python.exe，未配置时使用启动网页的Python |
| MATLAB_ROOT | 本机MATLAB安装目录，需安装兼容当前Python的MATLAB Engine及模型依赖产品 |
| TI_CGT_ROOT | TI C2000编译器根目录，包含bin/cl2000.exe |
| CAP_CLANG / CAP_CL2000 | CNN模型核验/目标编译使用的编译器完整路径 |

训练依赖见 requirements-training.txt，可安装到独立环境。TI ANN 编译还需要TI MCU NNC专用环境及量化/TVM工具；网站离线依赖不包含这些工具。MATLAB Engine 可在本机 MATLAB 的 extern/engines/python 目录用对应Python执行 `python -m pip install .`。网页仿真默认使用随包6个模型中的相应模型。

firmware/PI_ANN_CNN_SCI_Web_Monitor 为配套控制工程，保留CPU1_FLASH构建输入、模型库和原有控制代码。导入CCS后配置本机C2000Ware、SysConfig、编译器；网页不会自动烧录。模型库mod.a是必要输入，不是缓存。本次整理未改动控制器C源文件。

## 目录与维护入口

| 位置 | 职责和修改方法 |
|---|---|
| run.py / settings.py | 启动、端口选择、统一配置；优先在此处理环境问题 |
| webapp/app.py | Flask入口、欢迎页与本机接口边界 |
| webapp/project_portal.py | 功能路由、资源白名单、共享服务注册 |
| webapp/database_routes.py / csv_data_manager.py | 请求校验、CSV读写和目录授权 |
| webapp/templates / static/project | 页面结构、样式、交互；vendor子目录为第三方资源 |
| webapp/serial_service.py / static/project/serial.js | 唯一串口连接、协议解析、分功能显示 |
| webapp/training_runner.py / capacitance_training.py | 后台任务、数据快照、日志、状态与产物下载 |
| webapp/simulation_runner.py / simulink_bridge | MATLAB调用、模型/示波器与仿真状态 |
| webapp/scope_waveforms.py | 只读波形窗口查询与保留峰值的降采样 |
| webapp/ai_providers.py / ai_service.py / ai_prompts.py | 提供商适配、对话编排、分场景提示词 |
| webapp/ai_history.py / ai_attachments.py / ai_files.py | 历史加密、附件解码、文件生成与下载 |
| project_data/Code/python | 当前ANN、CPU/NPU编译、数据预处理以及scope_cap CNN流水线 |
| project_data/InputData / OutputData / Simulink | 默认数据、已保存结果、必要模型；不是临时缓存 |
| firmware / webapp/tests / .vscode | 配套控制工程、回归测试、可共享编辑器配置 |
| wheels | Windows x64 / Python3.10基础依赖离线包 |

代码已统一Python排版，关键模块和服务方法增加维护契约注释。增加新功能时保持“路由校验与响应→服务管理状态→外部工具执行”的结构；不要在导入模块时启动训练、连接硬件或运行仿真。长任务使用现有后台任务模式，前端按游标查询日志。新增AI提供商集中修改ai_providers，新增串口字段同步修改协议解析和serial.js，保留CRC与唯一连接约束。

## 精简和验证

已移除未被当前功能引用的旧训练/编译脚本、重复Simulink模型、重复说明与历史报告。保留默认数据、模型权重、测试和第三方许可证。第三方归属集中在 THIRD_PARTY_NOTICES.md，未批量改写其源码。

首次运行生成 webapp/runtime，其中有历史、任务、上传内容和会话密钥。停服备份后再清理；不要将该目录、config.json、.venv或API Key上传GitHub。.gitignore已排除这些内容。共享的.vscode配置保留用于复现。

测试不连接开发板、不调用收费模型API、不运行真实MATLAB或TI编译。一个依赖历史NPU硬件编译产物的测试允许明确跳过；真实硬件和外部工具需在相应环境另行验收。预期失败用例可能在终端输出错误日志，以最终测试结果为准。

交付验证：独立虚拟环境离线安装与 pip check 通过；134项回归中133项通过、1项历史NPU产物检查跳过；12个主要页面实际启动成功；自有JavaScript语法检查通过。控制器源码及模型库与上一版逐文件一致。
