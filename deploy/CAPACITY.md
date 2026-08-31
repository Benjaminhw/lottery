# 容量实测

测试日期：2026-09-01。本机代理测试用于观察应用极限，正式公网测试用于观察宾客实际首屏体验。压测客户端与服务端运行在同一台机器，因此 CPU 数据偏保守；公网请求仍经过云主机公网入口。

## 服务器基线

- 阿里云 `ecs.e-c1m1.large`，2 vCPU
- 操作系统可见 1.6 GiB 内存，4 GiB Swap
- 40 GiB 系统盘，约 29 GiB 可用
- Nginx `worker_connections 768`
- Uvicorn 单进程，SQLite WAL
- 前端资源启用 Gzip 与 1 小时浏览器缓存，约从 90 KiB 降至 20 KiB/客户端
- 测试时 VS Code Remote 与 Pylance 占用约 1 GiB 内存

每个模拟宾客都会加载 HTML、CSS、JavaScript，读取婚礼和个人状态，建立 SSE 实时连接，提交唯一姓名，并保持连接 3 秒。静态资源按首次访问计算，没有利用浏览器缓存。

## 结果

| 入口 | 同时会话 | 成功率 | 完整流程 p95 | 签到 p95 | SSE 首包 p95 | 应用峰值内存 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 本机代理 | 20 | 100% | 2.97 s | 277 ms | 133 ms | 59.8 MiB |
| 本机代理 | 100 | 100% | 7.81 s | 881 ms | 670 ms | 66.2 MiB |
| 本机代理 | 200 | 100% | 15.45 s | 4.49 s | 3.86 s | 69.6 MiB |
| 正式公网、Gzip | 100 | 100% | 15.83 s | 47 ms | 1.85 s | 62.8 MiB |

正式公网测试中，JavaScript 与 CSS 压缩后合计约 20.1 KiB/人，100 人约 2 MiB；静态资源 p95 为 10.09 秒，表现符合约 1-2 Mbps 有效公网出口。具体云带宽套餐仍需在阿里云控制台确认。此时应用进程 CPU 约 17%、签到接口 p95 仅 47 ms，说明生产体验的首要瓶颈是公网带宽，不是 CPU、内存或 SQLite。

200 会话本机测试也全部成功，但等待进入 4 秒级，说明它是压力上界而不是舒适容量。

## 建议

- 当前机器能承载 **100 人同时扫码签到且无错误**；若要求首次扫码约 5 秒内进入，按当前公网表现更适合约 20-30 人同秒突发。100 人在一两分钟内陆续扫码没有问题。
- 100 人真正同秒扫码，建议至少 2 vCPU、2 GiB 内存、10 GiB SSD、2 GiB Swap、5 Mbps 公网带宽；10 Mbps 或以上更稳妥。当前 CPU 与内存无需升级，优先提升公网带宽。
- 现场前关闭 VS Code Remote 与 Pylance，可释放约 1 GiB 内存。
- Nginx 建议将 `worker_connections` 提高到 1024，应用 `LimitNOFILE` 保持 4096；SSE 必须关闭代理缓冲并保留长读取超时。
- 现场 Wi-Fi、蜂窝网络和外部微信 OAuth 往往比本机接口更慢，应保留姓名签到作为兜底。
- 超过 300 个长期在线客户端或多场婚礼同时运行时，建议 4 vCPU、4 GiB 内存，并迁移到 PostgreSQL + Redis 后再启用多个 Uvicorn worker。

复测命令：

```bash
set -a; source .env; set +a
.venv313/bin/python deploy/load_test.py \
  --base-url https://8.133.234.5/wedding --clients 100 \
  --server-pid "$(systemctl --user show lottery.service -p MainPID --value)"
```