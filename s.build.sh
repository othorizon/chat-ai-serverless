# 执行方式：s build --script-file s.build.sh --publish-layer
# 如果不发布layer，而不是采用 deploy 跟着部署。要注意修改 .fcignore 文件，这个文件里忽略了 agora sdk 下载的 .so 文件，这会导致这些文件不会被部署
# 安装
pip install -t python -r requirements.txt --upgrade

# 现在这个有问题，到了服务端好像会有兼容问题，导致服务端的 asr 识别不准确，下次改为从服务端实际运行一份后再下载
# # 启动一次让agora 下载相关 sdk，避免每次启动服务都要下载占用时间 要注意系统架构和服务器保持一致
# export PYTHONPATH=/code/python
# export PATH=/code/python/bin:/opt/python/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/code:/code/bin:/opt:/opt/bin
# python index.py