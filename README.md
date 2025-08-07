# CHAT AI SERVERLESS

这是一个基于 serverless 架构的语音对话式 AI 服务。可以简单的实现企业级的高并发部署。
该架构已经运行在了企业的生产项目中。

## TODO

- 整理开源代码
- 提供DEMO


## python环境

Python 3.12.11

## 部署

使用阿里云 云计算 FC 服务
通过 serverless dev 工具进行部署
https://help.aliyun.com/zh/functioncompute/fc-3-0/developer-reference/install-serverless-devs-and-docker
https://help.aliyun.com/zh/functioncompute/deploy-a-code-package-1

### 构建与部署

配置相关环境变量  
执行 `s build --script-file s.build.sh` 来进行本地构建  
`s deploy -t s.{env}.yaml` 进行部署  


## 依赖服务

### RTC

声网 RTC服务端： https://doc.shengwang.cn/doc/rtc-server-sdk/python/landing-page

### MQTT

EMQX Serveless 服务：
https://docs.emqx.com/zh/cloud/latest/connect_to_deployments/python_sdk.html


## 火山大模型语音合成

先返回 350 文本开始
然后是 352 开始返回语音端
最后是 351 文本结束
接下来是下一段 重复 350 - 352 .. 352 - 351

