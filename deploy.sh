#!/bin/bash

###############################################################################
# Docker MCP Gateway 部署脚本
# 功能：
#   - 自动下载最新版本
#   - 检查端口冲突并自动清理
#   - 重启进程
#   - 打印最近启动日志
#   - 健康检查
#   - 进程守护
#   - 日志管理
#   - Docker 环境检查
###############################################################################

set -euo pipefail

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 配置变量
PROJECT_NAME="docker-mcp-gateway"
# 自动检测项目目录（脚本所在目录）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-${SCRIPT_DIR}}"  # 允许通过环境变量覆盖
GITHUB_REPO="xray918/taoke_docker"
SERVICE_NAME="docker-mcp-gateway"
PID_FILE="${PROJECT_DIR}/.docker-mcp-gateway.pid"
LOG_FILE="${PROJECT_DIR}/logs/docker-mcp-gateway.log"
ERROR_LOG="${PROJECT_DIR}/logs/docker-mcp-gateway-error.log"
CONFIG_FILE="${PROJECT_DIR}/config/containers.yaml"
ENV_FILE="${PROJECT_DIR}/.env"
MAX_LOG_LINES=100

###############################################################################
# 加载 .env 文件配置
###############################################################################

load_env_file() {
    if [[ -f "${ENV_FILE}" ]]; then
        # 使用 set -a 自动导出变量，然后 source .env
        set -a
        source "${ENV_FILE}" 2>/dev/null || true
        set +a
    fi
}

# 先加载 .env 文件
load_env_file

# 设置默认值（.env 中的值会覆盖这些默认值）
PORT=${PORT:-18082}
HOST=${HOST:-"0.0.0.0"}
LOG_LEVEL=${LOG_LEVEL:-"INFO"}
CONFIG_DIR=${CONFIG_DIR:-"${PROJECT_DIR}/config"}
DATA_DIR=${DATA_DIR:-"${PROJECT_DIR}/data"}

# 导出环境变量供子进程使用
export PORT
export HOST
export LOG_LEVEL
export CONFIG_DIR
export DATA_DIR

# 创建必要目录
mkdir -p "${PROJECT_DIR}/logs"
mkdir -p "${PROJECT_DIR}/backups"
mkdir -p "${CONFIG_DIR}"
mkdir -p "${DATA_DIR}"

###############################################################################
# 工具函数
###############################################################################

log_info() {
    echo -e "${GREEN}[INFO]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2
}

log_debug() {
    if [[ "${DEBUG:-0}" == "1" ]]; then
        echo -e "${BLUE}[DEBUG]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1"
    fi
}

###############################################################################
# 环境检查
###############################################################################

check_environment() {
    log_info "检查运行环境..."
    
    # 检查 uv
    if ! command -v uv &> /dev/null; then
        log_error "uv 未安装，请先安装 uv: curl -LsSf https://astral.sh/uv/install.sh | sh"
        exit 1
    fi
    log_info "✓ uv 已安装: $(uv --version)"
    
    # 检查 Python
    if ! command -v python3 &> /dev/null; then
        log_error "Python3 未安装"
        exit 1
    fi
    log_info "✓ Python 已安装: $(python3 --version)"
    
    # 检查 Docker
    if ! command -v docker &> /dev/null; then
        log_error "Docker 未安装，请先安装 Docker"
        exit 1
    fi
    log_info "✓ Docker 已安装: $(docker --version)"
    
    # 检查 Docker 是否运行
    if ! docker info &> /dev/null; then
        log_error "Docker 未运行，请启动 Docker 服务"
        exit 1
    fi
    log_info "✓ Docker 服务运行中"
    
    # 检查 Git
    if ! command -v git &> /dev/null; then
        log_warn "Git 未安装，将无法自动更新代码"
    else
        log_info "✓ Git 已安装: $(git --version)"
    fi
    
    # 检查项目目录
    if [[ ! -d "${PROJECT_DIR}" ]]; then
        log_error "项目目录不存在: ${PROJECT_DIR}"
        exit 1
    fi
    log_info "✓ 项目目录存在: ${PROJECT_DIR}"
    
    # 检查配置文件
    if [[ ! -f "${CONFIG_FILE}" ]]; then
        log_warn "配置文件不存在: ${CONFIG_FILE}"
        log_info "将在首次运行时自动创建"
    else
        log_info "✓ 配置文件存在: ${CONFIG_FILE}"
    fi
    
    # 检查 .env 文件
    if [[ -f "${ENV_FILE}" ]]; then
        log_info "✓ .env 文件存在: ${ENV_FILE}"
        log_info "  当前配置: PORT=${PORT}, HOST=${HOST}, LOG_LEVEL=${LOG_LEVEL}"
    else
        log_warn ".env 文件不存在: ${ENV_FILE}，将使用默认配置"
    fi
}

###############################################################################
# Docker 相关功能
###############################################################################

check_docker_containers() {
    log_info "检查 Docker 容器状态..."
    
    # 检查 MCP 相关容器
    local mcp_containers=$(docker ps -a --filter "label=mcp.gateway=true" --format "{{.Names}}: {{.Status}}" 2>/dev/null || true)
    
    if [[ -n "${mcp_containers}" ]]; then
        log_info "MCP 网关管理的容器:"
        echo "${mcp_containers}" | while read line; do
            echo "  - ${line}"
        done
    else
        log_info "当前没有 MCP 网关管理的容器"
    fi
}

###############################################################################
# 端口管理
###############################################################################

check_port() {
    local port=$1
    log_info "检查端口 ${port} 是否可用..."
    
    if command -v lsof &> /dev/null; then
        local pid=$(lsof -ti:${port} 2>/dev/null || true)
        if [[ -n "${pid}" ]]; then
            log_warn "端口 ${port} 被进程 ${pid} 占用"
            return 1
        fi
    elif command -v netstat &> /dev/null; then
        if netstat -tuln 2>/dev/null | grep -q ":${port} "; then
            log_warn "端口 ${port} 已被占用"
            return 1
        fi
    elif command -v ss &> /dev/null; then
        if ss -tuln 2>/dev/null | grep -q ":${port} "; then
            log_warn "端口 ${port} 已被占用"
            return 1
        fi
    else
        log_warn "无法检查端口状态（lsof/netstat/ss 都不可用）"
        return 0
    fi
    
    log_info "✓ 端口 ${port} 可用"
    return 0
}

kill_port_process() {
    local port=$1
    log_info "清理端口 ${port} 上的进程..."
    
    if command -v lsof &> /dev/null; then
        local pids=$(lsof -ti:${port} 2>/dev/null || true)
        if [[ -n "${pids}" ]]; then
            for pid in ${pids}; do
                log_warn "终止进程 ${pid} (占用端口 ${port})"
                kill -TERM ${pid} 2>/dev/null || true
                sleep 1
                if kill -0 ${pid} 2>/dev/null; then
                    log_warn "进程 ${pid} 未响应 TERM 信号，使用 KILL"
                    kill -KILL ${pid} 2>/dev/null || true
                fi
            done
            sleep 2
            log_info "✓ 端口 ${port} 已清理"
        else
            log_info "✓ 端口 ${port} 未被占用"
        fi
    else
        log_warn "lsof 不可用，无法自动清理端口"
    fi
}

###############################################################################
# 进程管理
###############################################################################

get_pid() {
    if [[ -f "${PID_FILE}" ]]; then
        local pid=$(cat "${PID_FILE}" 2>/dev/null || echo "")
        if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
            echo "${pid}"
            return 0
        else
            # PID 文件存在但进程不存在，清理 PID 文件
            rm -f "${PID_FILE}"
        fi
    fi
    
    # 尝试通过进程名查找
    local pid=$(pgrep -f "docker-mcp-gateway" | head -n1 || echo "")
    if [[ -n "${pid}" ]]; then
        echo "${pid}"
        return 0
    fi
    
    return 1
}

is_running() {
    if get_pid > /dev/null 2>&1; then
        return 0
    else
        return 1
    fi
}

stop_service() {
    log_info "停止服务..."
    
    if ! is_running; then
        log_info "服务未运行"
        return 0
    fi
    
    local pid=$(get_pid)
    if [[ -n "${pid}" ]]; then
        log_info "停止进程 ${pid}..."
        kill -TERM "${pid}" 2>/dev/null || true
        
        # 等待进程退出
        local count=0
        while kill -0 "${pid}" 2>/dev/null && [[ ${count} -lt 10 ]]; do
            sleep 1
            count=$((count + 1))
        done
        
        if kill -0 "${pid}" 2>/dev/null; then
            log_warn "进程未响应 TERM 信号，使用 KILL"
            kill -KILL "${pid}" 2>/dev/null || true
            sleep 1
        fi
        
        rm -f "${PID_FILE}"
        log_info "✓ 服务已停止"
    fi
    
    # 清理端口
    kill_port_process "${PORT}"
}

start_service() {
    log_info "启动服务..."
    
    if is_running; then
        local pid=$(get_pid)
        log_warn "服务已在运行 (PID: ${pid})"
        return 1
    fi
    
    # 检查并清理端口
    if ! check_port "${PORT}"; then
        kill_port_process "${PORT}"
        sleep 2
    fi
    
    # 清空旧的错误日志，避免显示历史错误
    > "${ERROR_LOG}"
    log_info "已清空错误日志文件"
    
    # 备份配置文件
    if [[ -f "${CONFIG_FILE}" ]]; then
        local backup_file="${PROJECT_DIR}/backups/containers_$(date +%Y%m%d_%H%M%S).yaml"
        cp "${CONFIG_FILE}" "${backup_file}"
        log_info "配置文件已备份到: ${backup_file}"
    fi
    
    # 切换到项目目录
    cd "${PROJECT_DIR}"
    
    # 同步依赖
    log_info "同步依赖..."
    uv sync --quiet 2>/dev/null || uv sync
    
    # 启动服务
    log_info "启动命令: uv run docker-mcp-gateway"
    log_info "环境变量: PORT=${PORT}, HOST=${HOST}, LOG_LEVEL=${LOG_LEVEL}"
    
    # 启动服务，标准输出和错误输出分别记录
    nohup uv run docker-mcp-gateway \
        >> "${LOG_FILE}" 2>> "${ERROR_LOG}" &
    
    local pid=$!
    echo "${pid}" > "${PID_FILE}"
    
    # 等待服务启动
    log_info "等待服务启动 (PID: ${pid})..."
    sleep 3
    
    if is_running; then
        log_info "✓ 服务启动成功 (PID: ${pid})"
        log_info "服务地址: http://${HOST}:${PORT}"
        log_info "Dashboard: http://${HOST}:${PORT}/"
        log_info "API 文档: http://${HOST}:${PORT}/docs"
        
        # 等待日志文件生成
        sleep 2
        
        # 打印最近的启动日志
        echo ""
        log_info "=== 最近启动日志 ==="
        if [[ -f "${LOG_FILE}" ]]; then
            tail -n 30 "${LOG_FILE}" 2>/dev/null || true
        fi
        # 检查错误日志中是否有真正的错误（排除 INFO/WARNING 级别的正常日志）
        if [[ -f "${ERROR_LOG}" ]] && [[ -s "${ERROR_LOG}" ]]; then
            # 检查是否包含真正的错误关键词
            if grep -qiE "(error|exception|traceback|fatal|failed|失败)" "${ERROR_LOG}" 2>/dev/null; then
                echo ""
                log_warn "=== 错误日志 ==="
                tail -n 20 "${ERROR_LOG}" 2>/dev/null || true
            fi
        fi
        echo ""
        
        return 0
    else
        log_error "服务启动失败，请查看日志: ${ERROR_LOG}"
        if [[ -f "${ERROR_LOG}" ]]; then
            echo ""
            log_error "=== 错误日志 ==="
            tail -n 30 "${ERROR_LOG}" 2>/dev/null || true
            echo ""
        fi
        rm -f "${PID_FILE}"
        return 1
    fi
}

restart_service() {
    log_info "重启服务..."
    stop_service
    sleep 2
    # 清空错误日志（确保清理历史错误）
    > "${ERROR_LOG}"
    log_info "已清空错误日志文件"
    start_service
}

###############################################################################
# 代码更新
###############################################################################

update_code() {
    log_info "更新代码..."
    
    if [[ ! -d "${PROJECT_DIR}/.git" ]]; then
        log_warn "项目目录不是 Git 仓库，跳过代码更新"
        return 1
    fi
    
    cd "${PROJECT_DIR}"
    
    # 备份当前更改
    if git diff --quiet && git diff --cached --quiet; then
        log_info "工作区干净，无需备份"
    else
        log_warn "检测到未提交的更改，创建备份..."
        git stash push -m "Auto backup before update $(date +%Y%m%d_%H%M%S)"
    fi
    
    # 获取最新代码
    log_info "拉取最新代码..."
    git fetch origin
    
    # 检查是否有更新
    local current_commit=$(git rev-parse HEAD)
    local remote_commit=$(git rev-parse origin/main 2>/dev/null || git rev-parse origin/master 2>/dev/null || echo "")
    
    if [[ -z "${remote_commit}" ]]; then
        log_warn "无法获取远程分支信息"
        return 1
    fi
    
    if [[ "${current_commit}" == "${remote_commit}" ]]; then
        log_info "✓ 代码已是最新版本"
        return 0
    fi
    
    log_info "发现新版本，正在更新..."
    git pull origin main 2>/dev/null || git pull origin master 2>/dev/null || {
        log_error "代码更新失败"
        return 1
    }
    
    # 更新依赖
    log_info "更新依赖..."
    uv sync
    
    log_info "✓ 代码更新完成"
    return 0
}

###############################################################################
# 日志管理
###############################################################################

show_logs() {
    local lines=${1:-${MAX_LOG_LINES}}
    log_info "显示最近 ${lines} 行日志..."
    
    if [[ -f "${LOG_FILE}" ]]; then
        echo -e "${BLUE}=== 应用日志 ===${NC}"
        tail -n "${lines}" "${LOG_FILE}"
    else
        log_warn "日志文件不存在: ${LOG_FILE}"
    fi
    
    if [[ -f "${ERROR_LOG}" ]]; then
        echo -e "\n${RED}=== 错误日志 ===${NC}"
        tail -n "${lines}" "${ERROR_LOG}"
    fi
}

show_startup_logs() {
    log_info "显示启动日志..."
    show_logs 50
}

follow_logs() {
    log_info "实时跟踪日志 (Ctrl+C 退出)..."
    if [[ -f "${LOG_FILE}" ]]; then
        tail -f "${LOG_FILE}"
    else
        log_warn "日志文件不存在: ${LOG_FILE}"
    fi
}

###############################################################################
# 健康检查
###############################################################################

health_check() {
    log_info "执行健康检查..."
    
    # 检查进程
    if ! is_running; then
        log_error "✗ 服务未运行"
        return 1
    fi
    local pid=$(get_pid)
    log_info "✓ 进程运行正常 (PID: ${pid})"
    
    # 检查端口是否在监听（端口被占用说明服务在运行）
    if check_port "${PORT}" 2>/dev/null; then
        log_warn "✗ 端口 ${PORT} 未被服务占用"
    else
        log_info "✓ 端口 ${PORT} 正常监听"
    fi
    
    # 检查 HTTP 端点
    local health_url="http://127.0.0.1:${PORT}/api/health"
    if command -v curl &> /dev/null; then
        if curl -s -f -o /dev/null "${health_url}" 2>/dev/null; then
            log_info "✓ HTTP 端点响应正常: ${health_url}"
        else
            log_warn "✗ HTTP 端点无响应: ${health_url}"
        fi
    elif command -v wget &> /dev/null; then
        if wget -q -O /dev/null "${health_url}" 2>/dev/null; then
            log_info "✓ HTTP 端点响应正常: ${health_url}"
        else
            log_warn "✗ HTTP 端点无响应: ${health_url}"
        fi
    else
        log_warn "curl/wget 不可用，跳过 HTTP 检查"
    fi
    
    # 检查 Docker
    if docker info &> /dev/null; then
        log_info "✓ Docker 服务正常"
        check_docker_containers
    else
        log_error "✗ Docker 服务异常"
    fi
    
    log_info "健康检查完成"
}

###############################################################################
# 状态查看
###############################################################################

show_status() {
    log_info "服务状态:"
    
    if is_running; then
        local pid=$(get_pid)
        echo -e "  状态: ${GREEN}运行中${NC}"
        echo -e "  PID: ${pid}"
        echo -e "  端口: ${PORT}"
        echo -e "  地址: http://${HOST}:${PORT}"
        echo -e "  Dashboard: http://${HOST}:${PORT}/"
        echo -e "  API 文档: http://${HOST}:${PORT}/docs"
        
        # 显示进程信息
        if command -v ps &> /dev/null; then
            echo -e "  进程信息:"
            ps -p "${pid}" -o pid,ppid,cmd,etime,pcpu,pmem 2>/dev/null || true
        fi
    else
        echo -e "  状态: ${RED}未运行${NC}"
    fi
    
    # 显示配置文件
    if [[ -f "${CONFIG_FILE}" ]]; then
        echo -e "  配置文件: ${CONFIG_FILE}"
    fi
    
    # 显示日志文件
    if [[ -f "${LOG_FILE}" ]]; then
        local log_size=$(du -h "${LOG_FILE}" | cut -f1)
        echo -e "  日志文件: ${LOG_FILE} (${log_size})"
    fi
    
    # 显示 Docker 信息
    echo ""
    check_docker_containers
}

###############################################################################
# 安装服务（系统服务）
###############################################################################

install_systemd_service() {
    log_info "安装 systemd 服务..."
    
    if [[ ! -d "/etc/systemd/system" ]]; then
        log_error "系统不支持 systemd"
        return 1
    fi
    
    local service_file="/etc/systemd/system/docker-mcp-gateway.service"
    
    cat > /tmp/docker-mcp-gateway.service << EOF
[Unit]
Description=Docker MCP Gateway Service
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=${USER}
WorkingDirectory=${PROJECT_DIR}
Environment=PORT=${PORT}
Environment=HOST=${HOST}
Environment=LOG_LEVEL=${LOG_LEVEL}
Environment=CONFIG_DIR=${CONFIG_DIR}
Environment=DATA_DIR=${DATA_DIR}
ExecStart=$(which uv) run docker-mcp-gateway
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

    sudo mv /tmp/docker-mcp-gateway.service "${service_file}"
    sudo systemctl daemon-reload
    sudo systemctl enable docker-mcp-gateway
    
    log_info "✓ systemd 服务已安装"
    log_info "使用以下命令管理服务:"
    log_info "  sudo systemctl start docker-mcp-gateway"
    log_info "  sudo systemctl stop docker-mcp-gateway"
    log_info "  sudo systemctl status docker-mcp-gateway"
}

###############################################################################
# 主函数
###############################################################################

usage() {
    cat << EOF
用法: $0 <command> [options]

命令:
  start           启动服务
  stop            停止服务
  restart         重启服务
  status          查看服务状态
  update          更新代码到最新版本
  deploy          部署（更新代码 + 重启服务）
  logs            查看日志 (默认最近 ${MAX_LOG_LINES} 行)
  logs-follow     实时跟踪日志
  logs-startup    查看启动日志
  health          健康检查
  clean           清理日志和临时文件
  install         安装为 systemd 服务（Linux）

环境变量:
  PORT            服务端口 (默认: ${PORT})
  HOST            服务地址 (默认: ${HOST})
  LOG_LEVEL       日志级别 (默认: ${LOG_LEVEL})
  CONFIG_DIR      配置目录 (默认: ${CONFIG_DIR})
  DATA_DIR        数据目录 (默认: ${DATA_DIR})
  DEBUG           调试模式 (1 启用, 0 禁用)

示例:
  $0 start                    # 启动服务
  $0 restart                  # 重启服务
  $0 deploy                   # 部署最新版本
  $0 logs 200                 # 查看最近 200 行日志
  $0 health                   # 健康检查
  PORT=8080 $0 start          # 使用自定义端口启动

EOF
}

main() {
    local command=${1:-}
    
    case "${command}" in
        start)
            check_environment
            start_service
            ;;
        stop)
            stop_service
            ;;
        restart)
            check_environment
            restart_service
            ;;
        status)
            show_status
            ;;
        update)
            update_code
            ;;
        deploy)
            check_environment
            update_code
            restart_service
            ;;
        logs)
            show_logs ${2:-${MAX_LOG_LINES}}
            ;;
        logs-follow)
            follow_logs
            ;;
        logs-startup)
            show_startup_logs
            ;;
        health)
            health_check
            ;;
        clean)
            log_info "清理日志和临时文件..."
            rm -f "${PID_FILE}"
            find "${PROJECT_DIR}/logs" -name "*.log" -mtime +7 -delete 2>/dev/null || true
            find "${PROJECT_DIR}/backups" -name "*.yaml" -mtime +30 -delete 2>/dev/null || true
            log_info "✓ 清理完成"
            ;;
        install)
            install_systemd_service
            ;;
        *)
            usage
            exit 1
            ;;
    esac
}

# 执行主函数
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
