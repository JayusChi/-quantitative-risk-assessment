# Local workspace

此目录只保存本机业务数据和运行产物，不属于应用源码：

- `inputs/`：待转换源资料、已确认 JSON 和任务配置；
- `outputs/`：文件版计算输出；
- `state/`：SQLite 数据库等持久化运行状态；
- `runtime/`：一次性计算临时目录。

可通过环境变量 `QRA_WORKSPACE_ROOT` 把整套运行数据迁移到其他磁盘或部署挂载点。

