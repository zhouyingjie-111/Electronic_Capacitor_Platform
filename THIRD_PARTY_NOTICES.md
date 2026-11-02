# 第三方组件与发布范围

本包不替第三方组件重新授权。保留文件内的原始版权、许可及归属声明。

- `webapp/static/project/vendor`：Marked、DOMPurify、KaTeX，包含对应 LICENSE 及字体资源。
- `webapp/vendor_public`：pypdf、ReportLab 及其附带依赖，许可文件随目录保留。
- `firmware` 内 TI 设备支持、生成代码及模型库：按原始文件声明和 TI 工具/SDK 条款使用；CCS、C2000Ware、SysConfig、MATLAB、TI MCU NNC 本身不包含在本包，也不因本项目开源而成为本项目自有代码。
- Python 依赖由 pip 安装，各依赖遵循自身许可。
- 默认数据、图片和 Simulink 模型来自现有项目素材。上传公开仓库前，项目拥有者应确认其分享权限；此整理工作没有新增第三方授权。

项目拥有者已选择 MIT 许可。根目录 LICENSE 适用于项目拥有者有权授权的自有源码和自有说明文档；不替代上述第三方文件原有许可证，也不自动给默认数据、图像、Simulink素材、TI支持文件或模型库增加新的授权。对于混合了TI设备支持代码的firmware文件，原TI版权与适用限制继续保留。
