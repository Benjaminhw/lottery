# 幸运现场

一个可自行部署的扫码报名与现场抽奖应用。管理员可以配置多场活动、多轮奖项、每轮奖品和中奖人数；参与者扫码后使用姓名或微信昵称报名，抽奖结果由服务端安全随机产生并固化。

## 已实现

- 活动管理、报名开关、动态轮次与奖品配置
- 自动生成报名二维码，名单实时推送到管理台和抽奖大屏
- 姓名报名，以及微信公众号 `snsapi_userinfo` 昵称/头像授权
- 服务端原子抽奖、按轮次执行、跨轮次不重复、重复请求不重抽
- 候选头像滚动、减速揭晓、多中奖者展示、全屏与结果回看
- SQLite 持久化、管理员会话、生产环境安全校验、Docker 部署

## 本地运行

要求 Python 3.11 或更高版本。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[test]"
Copy-Item .env.example .env
uvicorn app.main:app --reload
```

打开 `http://127.0.0.1:8000/admin`。开发模板的管理密码是 `change-me`，仅可本地使用。

## 服务器部署

1. 将项目上传到服务器，安装 Docker 与 Docker Compose。
2. 复制环境变量模板：`cp .env.example .env`。
3. 修改 `.env`：
   - `ADMIN_PASSWORD`：强管理密码。
   - `SECRET_KEY`：运行 `openssl rand -hex 32` 生成。
   - `PUBLIC_BASE_URL`：外部可访问的 HTTPS 地址，例如 `https://lottery.example.com`。
4. 启动：`docker compose up -d --build`。
5. 将 Nginx/Caddy 反向代理到 `127.0.0.1:8000`，配置 TLS。Nginx 起点见 `deploy/nginx.conf.example`。
6. 访问 `https://你的域名/admin` 创建活动。

Compose 只把应用绑定到本机回环地址，公网流量应始终经过 HTTPS 反向代理。生产模式使用默认密码或默认密钥时，应用会拒绝启动。

## 微信昵称和头像

浏览器不能仅凭“微信扫码”直接读取用户资料。自动获取微信昵称与头像需要一个具备网页授权能力的微信公众号，并完成以下配置：

1. 在微信公众平台配置“网页授权域名”，只填域名，不含协议和路径。
2. 设置 `.env` 中的 `WECHAT_APP_ID` 与 `WECHAT_APP_SECRET`。
3. 将 `PUBLIC_BASE_URL` 设为该授权域名对应的 HTTPS 地址。
4. 重启容器：`docker compose up -d`。

应用使用回调地址 `https://你的域名/auth/wechat/callback`，授权范围为 `snsapi_userinfo`。未配置微信参数时，微信报名按钮自动隐藏，姓名报名仍可正常使用。普通个人微信、未开通网页授权的订阅号或仅有一台服务器，都无法绕过微信平台的这项限制。

## 现场流程

1. 管理员创建活动并设置每轮奖品与人数。
2. 展示或下载二维码，参与者扫码报名。
3. 人数确认后关闭报名，进入抽奖大屏并切换全屏。
4. 按顺序开奖。第一次开奖也会自动关闭报名入口。
5. 管理台可以查看结果；如需彩排重来，可重置全部结果，报名名单会保留。

## 数据与运维

- 数据库默认位于 Docker 卷 `lottery-data` 中；正式活动前后请备份该卷。
- 微信 OpenID 仅用于同场活动防重复，不会由公开 API 返回。昵称和头像会显示给持有活动链接的用户。
- 姓名报名无法可靠识别同名的不同人员。当前策略是同一活动内姓名唯一；同名者可附加部门或编号。
- 当前部署针对单机活动场景，Uvicorn 固定一个工作进程。超大规模或多机部署应把 SQLite 和事件流替换为 PostgreSQL 与 Redis。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```