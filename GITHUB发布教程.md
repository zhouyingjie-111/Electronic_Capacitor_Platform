# GitHub 开源发布教程

本目录已经包含完整项目代码、README、MIT LICENSE、第三方许可说明、.gitignore、VS Code 配置和 GitHub Actions 工作流。目标仓库为 https://github.com/zhouyingjie-111/AI-Power-Electronics-Platform。仓库已经创建；首次发布方法及后续更新方法保留在下文，便于复现。

## 1. 建立空仓库

登录自己的 GitHub，在 [新建仓库页面](https://github.com/new) 操作：

- Owner 选择自己的账号。
- Repository name 可用 `electronic-capacitor-platform`，也可使用你已经确定的项目名。
- Description 可填：`双通道互济型电子电容智能控制与状态监测平台：ANN快速追踪、CNN电容辨识、Simulink仿真与串口交互。`
- Visibility 选择 **Public**。
- **不要勾选初始化 README、.gitignore 或 License**，本地已经有这些文件。
- 点击 Create repository，复制 HTTPS 仓库地址，例如 `https://github.com/zhouyingjie-111/AI-Power-Electronics-Platform.git`。

如果新项目已创建且为空，直接使用它的地址。如果其中已有 README 或其他提交，使用下面第5节的方法，避免首次推送历史冲突。步骤依据 [GitHub 官方本地代码导入指南](https://docs.github.com/en/migrations/importing-source-code/using-the-command-line-to-import-source-code/adding-locally-hosted-code-to-github)。

## 2. 打开正确的项目目录

完整解压本包，在 VS Code 中打开包含 `run.py` 和 `LICENSE` 的这一层目录，再打开终端。不要在外层“AI-based optimized design”大目录执行 Git 操作，不要只上传压缩包。

确认终端所在位置：

```powershell
Get-Location
Get-ChildItem -Force
git --version
```

## 3. 本地提交

以下 `user.name` / `user.email` 是本仓库的提交署名，不是 GitHub 密码；可填写 GitHub 提供的隐私邮箱。

```powershell
git init -b main
git config user.name "你的提交署名"
git config user.email "你的GitHub提交邮箱"
git add .
git status --short
```

提交前确认下列情况：

- `.venv`、`webapp/runtime`、聊天记录、API Key、根目录 `config.json` 不在待提交列表。
- **`project_data/Code/python/scope_cap/config.json` 必须在待提交列表**，这是默认算法配置，不是私人配置。
- 默认数据、Simulink 模型、必要模型库、`.vscode` 和 `.github` 已包含。

然后提交：

```powershell
git commit -m "Initial open source release"
```

## 4. 推送到你的仓库

将下面 URL 替换为实际仓库地址，不要直接使用示例：

```powershell
git remote add origin https://github.com/zhouyingjie-111/AI-Power-Electronics-Platform.git
git remote -v
git push -u origin main
```

如果弹出 GitHub 登录或授权，请本人在浏览器中完成，不需要把密码或Token发给助手。HTTPS认证使用 Git Credential Manager 或 GitHub 支持的凭据方式，账户密码不能直接代替Git推送认证。详见 [GitHub 认证说明](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/about-authentication-to-github)。

仓库地址错误时，使用 `git remote set-url origin 你的正确仓库地址`；不要因此重建或覆盖已有远端仓库。

## 5. 如果新仓库已有 README 或其他提交

推荐另建目录克隆远端，再把本包内容复制到克隆目录内。保留克隆目录里的 `.git`，不要复制本机 `.venv`、运行记录或根目录私人配置。这样保留已有历史，无需强制推送。

```powershell
git clone https://github.com/你的用户名/你的仓库名.git electronic-capacitor-publish
cd electronic-capacitor-publish
# 在文件管理器中将本包内容复制到此目录，保留这里的 .git
git add .
git status --short
git commit -m "Add electronic capacitor platform"
git push
```

已有文件同名时先比较内容；如果远端已有其他许可证，先确认许可兼容性，不要直接覆盖。不要使用 `git push --force` 处理这类首次发布问题。

## 6. 关于大文件

本包有约90 MiB的默认示波器CSV，因此推荐Git命令行或GitHub Desktop，不建议把完整项目拖入网页上传。GitHub浏览器上传单文件上限25 MiB，普通Git推送会对超过50 MiB的文件提示警告，超过100 MiB会阻止。本次扫描没有超过100 MiB的源文件，当前这批数据无需因大小强制使用LFS。规则见 [GitHub 大文件说明](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)。

不要为了消除大文件提示删除默认CSV，否则克隆后默认数据功能会缺失。将来增加超过100 MiB的文件时再使用Git LFS或提供Release下载与完整的获取说明。

## 7. 发布后检查

在 GitHub 仓库页面确认：README 正常显示、LICENSE 被识别为 MIT、`.vscode` 和 CNN配置存在、私人运行记录未上传。Actions 中的 Website checks 会进行基础页面和测试检查。

再从GitHub克隆到一个新目录，按README安装依赖并运行 `python run.py --check`，验证仓库不依赖你原来的桌面路径。MATLAB、TI工具链和开发板仍需用户自己配置，GitHub Pages不能直接运行本项目的Python服务、串口或本机MATLAB。

## 8. 许可证与版权

已按你的选择加入MIT许可证，项目贡献者以 `Electronic Capacitor Platform contributors` 署名；发布前可改为实际权利人或团队名称。

MIT适用于你有权授权的项目自有代码。第三方库、TI文件、模型库、默认数据和图像的范围见 THIRD_PARTY_NOTICES.md，它们不因根目录MIT文件自动改变许可。公开前需确认这些项目素材的分享权限。GitHub关于许可证的说明见 [Licensing a repository](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository)。

## 9. 后续更新

```powershell
git status
git add .
git diff --cached --stat
git commit -m "Describe the change"
git push
```

不要提交API Key、个人配置、聊天历史或模型训练中的临时日志。根目录私人配置由 `/config.json` 忽略，算法目录的config.json则保留。
