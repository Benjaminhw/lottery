# 喜礼现场

面向婚礼现场的扫码签到与喜礼抽奖应用。宾客扫码后使用姓名或微信昵称签到，管理端实时看到来宾名单并按轮次开奖；结果由服务端安全随机产生、持久化且跨轮不重复。

## 现场能力

- 多场婚礼、签到开关、喜礼轮次、奖品和人数配置
- 自动生成签到二维码，宾客名单实时推送到管理台与喜礼大屏
- 姓名签到，以及微信公众号 `snsapi_userinfo` 昵称/头像授权
- 服务端原子抽奖、顺序开奖、跨轮不重复、重复请求不重抽
- 候选头像滚动、减速揭晓、多中奖者展示、全屏与结果回看
- SQLite WAL 持久化、管理员会话、生产安全校验、Docker 或 systemd 部署
- 同场婚礼共享一条数据库轮询，多个 SSE 客户端复用快照

## 婚礼流程

1. 管理员创建婚礼，默认带“欢喜伴手礼、甜蜜幸运奖、幸福锦鲤”三轮，可按实际奖品修改。
2. 展示或下载签到二维码，宾客扫码填写姓名或使用微信资料签到。
3. 人数确认后关闭签到，打开喜礼大屏并切换全屏。
4. 按顺序开奖；第一次开奖也会自动关闭签到入口。
5. 在管理台查看结果。彩排后可重置全部结果，签到名单会保留。

## 现场操作与启停

### 开启或关闭宾客签到

1. 访问 `https://wedding.example.com/wedding/admin` 并登录管理台。
2. 打开需要操作的婚礼场次。
3. 使用“扫码签到”开关开启或关闭签到入口。

关闭签到后，新宾客不能加入，但已有签到名单与抽奖结果会保留。第一次开奖也会自动关闭签到入口。

### 找到并扫描二维码

进入管理台的婚礼详情页，桌面端右侧会显示“扫码签到”二维码；手机端管理页中，二维码位于主要内容下方。可以现场直接展示二维码，也可以点击“下载”后投放到迎宾屏或打印物料。

宾客使用微信“扫一扫”或手机相机扫描即可。签到链接格式为：

```text
https://wedding.example.com/wedding/e/婚礼代码
```

### 开启或关闭整个应用

```bash
# 查看状态
systemctl --user status lottery.service

# 开启、关闭或重启
systemctl --user start lottery.service
systemctl --user stop lottery.service
systemctl --user restart lottery.service

# 开启或关闭登录后自动运行
systemctl --user enable lottery.service
systemctl --user disable lottery.service
```

停止应用后，Nginx 的 `/wedding/` 公网入口仍然存在，但会暂时无法访问；重新启动服务即可恢复，签到名单与开奖结果不会丢失。

## 本地运行

要求 Python 3.11 或更高版本。

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
uvicorn app.main:app --reload
```

访问 `http://localhost:8000/admin`。开发模板密码 `change-me` 只能用于本地。

## 当前服务器部署

当前服务器使用用户级 systemd 运行应用，并通过已有 Nginx 的 `/wedding/` 子路径提供 HTTPS：

```bash
mkdir -p ~/.config/systemd/user
ln -sf ~/Learning/lottery/deploy/lottery.service ~/.config/systemd/user/lottery.service
systemctl --user daemon-reload
systemctl --user enable --now lottery.service
```

安装独立的 Nginx 站点文件。它只在配置的 HTTPS 域名下接管 `/wedding/`，其余路径继续转给现有服务：

```bash
sudo install -m 644 deploy/nginx-wedding-site.conf /etc/nginx/conf.d/wedding.conf
sudo nginx -t
sudo systemctl reload nginx
```

生产 `.env` 关键项：

```dotenv
APP_ENV=production
PUBLIC_BASE_URL=https://wedding.example.com/wedding
BASE_PATH=/wedding
DATABASE_PATH=./data/lottery.db
COOKIE_SECURE=true
```

`ADMIN_PASSWORD` 与 `SECRET_KEY` 必须使用随机强值。生产模式发现默认值时会拒绝启动。

## Docker 部署

```bash
cp .env.example .env
# 修改密码、密钥和外部 HTTPS 地址
docker compose up -d --build
```

Compose 只绑定本机回环接口的 8000 端口，公网流量应始终经过 HTTPS 反向代理。根路径部署时将 `BASE_PATH` 留空。

## 微信签到

仅扫描普通网页二维码不会向网站提供微信昵称和头像。自动读取资料必须通过微信网页授权，并满足以下条件：

1. 准备具备网页授权能力的微信公众号，并取得 AppID 与 AppSecret。
2. 准备解析到服务器的正式 HTTPS 域名。微信网页授权域名填写纯域名，例如 `wedding.example.com`，不含协议、端口或路径；公网 IP 不能代替网页授权域名。
3. 在微信公众平台的功能设置中，将该域名配置为“网页授权域名”。
4. 在服务器 `.env` 中填写配置，AppSecret 只在服务器上输入，不要提交到 Git：

```dotenv
PUBLIC_BASE_URL=https://wedding.example.com/wedding
WECHAT_APP_ID=公众号AppID
WECHAT_APP_SECRET=公众号AppSecret
```

5. 重启服务并检查公开状态：

```bash
systemctl --user restart lottery.service
curl https://wedding.example.com/wedding/api/events/婚礼代码
```

响应中的 `wechat_enabled` 应为 `true`。配置成功后，宾客在微信内扫描签到二维码会自动进入微信授权；同意后昵称与头像会写入签到名单。微信仍会显示官方授权确认页，应用不能绕过用户同意静默读取资料。

未配置微信参数时，微信签到入口自动隐藏，姓名签到仍可使用。普通个人微信、没有网页授权权限的公众号或只有公网 IP 的部署不能读取昵称与头像。

## 数据与测试

- 数据库位于 `DATABASE_PATH`；正式婚礼前后应备份数据库文件及 `-wal`、`-shm` 文件，或停服务后复制整个 `data/` 目录。
- 同一婚礼内姓名唯一；同名宾客可在姓名后附加关系或编号。
- 微信 OpenID 只用于防重复，不会由公开 API 返回。

```bash
.venv313/bin/python -m pytest
```

100/200 人并发实测数据与配置建议见 `deploy/CAPACITY.md`。