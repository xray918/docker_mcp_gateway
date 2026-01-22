#!/bin/bash

###############################################################################
# Docker MCP Gateway 远程部署脚本
# 功能：
#   - 从本地一键部署到远程服务器
#   - 自动同步代码（通过 Git 或 rsync）
#   - 远程执行部署命令
#   - 检查部署状态
###############################################################################

set -euo pipefail

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ==================== 配置区域（请根据实际情况修改）====================

# 远程服务器配置
REMOTE_USER="root"                              # SSH 用户名
REMOTE_HOST="8.217.130.241"                     # 服务器 IP 或域名
REMOTE_PORT="22"                                # SSH 端口
REMOTE_PATH="/root/docker_mcp_gateway"          # 远程项目路径
REMOTE_PASSWORD=""                              # SSH 密码（留空则使用密钥）

# 本地配置
LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GITHUB_REPO="xray918/taoke_docker"              # GitHub 仓库

# 部署模式: git / rsync
# git   - 服务器从 GitHub 拉取代码（推荐，需要服务器能访问 GitHub）
# rsync - 直接从本地同步代码到服务器（服务器无需访问 GitHub）
DEPLOY_MODE="git"

# ==================== 结束配置区域 ====================

# 加载本地 .env 文件（如果有远程配置）
if [[ -f "${LOCAL_DIR}/.env.remote" ]]; then
    source "${LOCAL_DIR}/.env.remote"
fi

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

log_step() {
    echo -e "\n${BLUE}==>${NC} $1"
}

# SSH 命令封装
ssh_cmd() {
    if [[ -n "${REMOTE_PASSWORD}" ]]; then
        sshpass -p "${REMOTE_PASSWORD}" ssh -o StrictHostKeyChecking=no -p "${REMOTE_PORT}" "${REMOTE_USER}@${REMOTE_HOST}" "$@"
    else
        ssh -p "${REMOTE_PORT}" "${REMOTE_USER}@${REMOTE_HOST}" "$@"
    fi
}

# SCP 命令封装
scp_cmd() {
    if [[ -n "${REMOTE_PASSWORD}" ]]; then
        sshpass -p "${REMOTE_PASSWORD}" scp -o StrictHostKeyChecking=no -P "${REMOTE_PORT}" "$@"
    else
        scp -P "${REMOTE_PORT}" "$@"
    fi
}

###############################################################################
# 检查配置
###############################################################################

check_config() {
    log_step "检查配置..."
    
    if [[ "${REMOTE_HOST}" == "your-server-ip" ]]; then
        log_error "请先配置远程服务器地址！"
        log_info "编辑 remote_deploy.sh 文件，修改以下配置："
        echo "  REMOTE_USER=\"your-username\""
        echo "  REMOTE_HOST=\"your-server-ip\""
        echo "  REMOTE_PATH=\"/path/to/project\""
        echo ""
        log_info "或创建 .env.remote 文件："
        echo "  REMOTE_USER=root"
        echo "  REMOTE_HOST=192.168.1.100"
        echo "  REMOTE_PATH=/opt/docker-mcp-gateway"
        exit 1
    fi
    
    log_info "远程服务器: ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PORT}"
    log_info "远程路径: ${REMOTE_PATH}"
    log_info "部署模式: ${DEPLOY_MODE}"
}

###############################################################################
# 检查 SSH 连接
###############################################################################

check_ssh() {
    log_step "检查 SSH 连接..."
    
    # 检查是否需要 sshpass
    if [[ -n "${REMOTE_PASSWORD}" ]] && ! command -v sshpass &> /dev/null; then
        log_error "使用密码认证需要安装 sshpass"
        log_info "安装方法："
        echo "  macOS: brew install sshpass"
        echo "  Ubuntu/Debian: apt-get install sshpass"
        echo "  CentOS/RHEL: yum install sshpass"
        exit 1
    fi
    
    if ! ssh_cmd "echo 'SSH 连接成功'" 2>/dev/null; then
        log_error "无法连接到服务器 ${REMOTE_USER}@${REMOTE_HOST}"
        log_info "请检查："
        echo "  1. 服务器地址是否正确"
        echo "  2. SSH 密码/密钥是否正确"
        echo "  3. 防火墙是否允许 SSH 连接"
        exit 1
    fi
    
    log_info "✓ SSH 连接正常"
}

###############################################################################
# 初始化远程环境
###############################################################################

init_remote() {
    log_step "初始化远程环境..."
    
    ssh_cmd << 'REMOTE_SCRIPT'
set -e

# 检查并安装 uv
if ! command -v uv &> /dev/null; then
    echo "安装 uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi
echo "✓ uv 已安装: $(uv --version)"

# 检查 Docker
if ! command -v docker &> /dev/null; then
    echo "ERROR: Docker 未安装，请先安装 Docker"
    exit 1
fi
echo "✓ Docker 已安装: $(docker --version)"

# 检查 Docker 服务
if ! docker info &> /dev/null; then
    echo "ERROR: Docker 服务未运行"
    exit 1
fi
echo "✓ Docker 服务运行中"

# 检查 Git
if ! command -v git &> /dev/null; then
    echo "WARN: Git 未安装"
fi

REMOTE_SCRIPT
    
    log_info "✓ 远程环境检查完成"
}

###############################################################################
# 同步代码
###############################################################################

sync_code_git() {
    log_step "通过 Git 同步代码..."
    
    ssh_cmd << REMOTE_SCRIPT
set -e

# 创建项目目录
mkdir -p "${REMOTE_PATH}"
cd "${REMOTE_PATH}"

# 检查是否已有 Git 仓库
if [[ -d ".git" ]]; then
    echo "更新现有仓库..."
    git fetch origin
    git reset --hard origin/main 2>/dev/null || git reset --hard origin/master
else
    echo "克隆仓库..."
    cd ..
    rm -rf "${REMOTE_PATH}"
    git clone "https://github.com/${GITHUB_REPO}.git" "${REMOTE_PATH}"
    cd "${REMOTE_PATH}"
fi

echo "✓ 代码同步完成"
git log -1 --oneline

REMOTE_SCRIPT
}

sync_code_rsync() {
    log_step "通过 rsync 同步代码..."
    
    # 创建远程目录
    ssh_cmd "mkdir -p ${REMOTE_PATH}"
    
    # 同步代码（排除不需要的文件）
    rsync -avz --progress \
        --exclude '.git' \
        --exclude '__pycache__' \
        --exclude '*.pyc' \
        --exclude '.venv' \
        --exclude 'venv' \
        --exclude 'logs' \
        --exclude 'backups' \
        --exclude '.env' \
        --exclude '*.pid' \
        --exclude 'uv.lock' \
        -e "ssh -p ${REMOTE_PORT}" \
        "${LOCAL_DIR}/" \
        "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PATH}/"
    
    log_info "✓ 代码同步完成"
}

sync_code() {
    case "${DEPLOY_MODE}" in
        git)
            sync_code_git
            ;;
        rsync)
            sync_code_rsync
            ;;
        *)
            log_error "未知的部署模式: ${DEPLOY_MODE}"
            exit 1
            ;;
    esac
}

###############################################################################
# 同步配置文件
###############################################################################

sync_config() {
    log_step "同步配置文件..."
    
    # 非交互模式，自动跳过配置同步（服务器上已有配置）
    log_info "跳过配置文件同步（保留服务器现有配置）"
    log_info "如需同步配置，请手动执行："
    if [[ -f "${LOCAL_DIR}/.env" ]]; then
        echo "  scp .env ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PATH}/.env"
    fi
    if [[ -f "${LOCAL_DIR}/config/containers.yaml" ]]; then
        echo "  scp config/containers.yaml ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_PATH}/config/"
    fi
}

###############################################################################
# 远程部署
###############################################################################

remote_deploy() {
    log_step "执行远程部署..."
    
    ssh_cmd << REMOTE_SCRIPT
set -e
cd "${REMOTE_PATH}"

# 确保 uv 在 PATH 中
export PATH="\$HOME/.local/bin:\$PATH"

# 给部署脚本执行权限
chmod +x deploy.sh

# 执行部署
./deploy.sh restart

echo ""
echo "=========================================="
echo "部署完成！"
echo "=========================================="

REMOTE_SCRIPT
}

###############################################################################
# 检查远程状态
###############################################################################

check_remote_status() {
    log_step "检查远程服务状态..."
    
    ssh_cmd << REMOTE_SCRIPT
cd "${REMOTE_PATH}" 2>/dev/null || exit 0
export PATH="\$HOME/.local/bin:\$PATH"

if [[ -f "deploy.sh" ]]; then
    ./deploy.sh status
    echo ""
    ./deploy.sh health
fi

REMOTE_SCRIPT
}

###############################################################################
# 查看远程日志
###############################################################################

show_remote_logs() {
    local lines=${1:-100}
    log_step "查看远程日志（最近 ${lines} 行）..."
    
    ssh_cmd << REMOTE_SCRIPT
cd "${REMOTE_PATH}"
export PATH="\$HOME/.local/bin:\$PATH"
./deploy.sh logs ${lines}
REMOTE_SCRIPT
}

follow_remote_logs() {
    log_step "实时跟踪远程日志 (Ctrl+C 退出)..."
    if [[ -n "${REMOTE_PASSWORD}" ]]; then
        sshpass -p "${REMOTE_PASSWORD}" ssh -o StrictHostKeyChecking=no -p "${REMOTE_PORT}" "${REMOTE_USER}@${REMOTE_HOST}" "cd ${REMOTE_PATH} && tail -f logs/docker-mcp-gateway.log"
    else
        ssh -p "${REMOTE_PORT}" "${REMOTE_USER}@${REMOTE_HOST}" "cd ${REMOTE_PATH} && tail -f logs/docker-mcp-gateway.log"
    fi
}

###############################################################################
# 停止远程服务
###############################################################################

stop_remote() {
    log_step "停止远程服务..."
    
    ssh_cmd << REMOTE_SCRIPT
cd "${REMOTE_PATH}"
export PATH="\$HOME/.local/bin:\$PATH"
./deploy.sh stop
REMOTE_SCRIPT
}

###############################################################################
# 主函数
###############################################################################

usage() {
    cat << EOF
用法: $0 [command]

命令:
  deploy          完整部署流程（初始化 + 同步代码 + 部署）【默认】
  sync            仅同步代码到服务器
  restart         重启远程服务
  stop            停止远程服务
  status          查看远程服务状态
  logs [N]        查看远程日志（默认最近 100 行）
  logs-follow     实时跟踪远程日志
  ssh             SSH 登录到服务器
  init            初始化远程环境
  help            显示帮助信息

配置:
  配置文件: .env.remote（从 .env.remote.example 复制）

示例:
  $0                  # 直接运行，执行完整部署
  $0 deploy           # 完整部署
  $0 status           # 查看状态
  $0 logs 200         # 查看最近 200 行日志
  $0 ssh              # 登录服务器

EOF
}

main() {
    local command=${1:-deploy}  # 默认执行 deploy
    
    case "${command}" in
        deploy)
            check_config
            check_ssh
            init_remote
            sync_code
            sync_config
            remote_deploy
            check_remote_status
            ;;
        sync)
            check_config
            check_ssh
            sync_code
            ;;
        restart)
            check_config
            check_ssh
            remote_deploy
            ;;
        stop)
            check_config
            check_ssh
            stop_remote
            ;;
        status)
            check_config
            check_ssh
            check_remote_status
            ;;
        logs)
            check_config
            check_ssh
            show_remote_logs ${2:-100}
            ;;
        logs-follow)
            check_config
            check_ssh
            follow_remote_logs
            ;;
        ssh)
            check_config
            log_info "连接到 ${REMOTE_USER}@${REMOTE_HOST}..."
            if [[ -n "${REMOTE_PASSWORD}" ]]; then
                sshpass -p "${REMOTE_PASSWORD}" ssh -o StrictHostKeyChecking=no -p "${REMOTE_PORT}" "${REMOTE_USER}@${REMOTE_HOST}"
            else
                ssh -p "${REMOTE_PORT}" "${REMOTE_USER}@${REMOTE_HOST}"
            fi
            ;;
        init)
            check_config
            check_ssh
            init_remote
            ;;
        help|--help|-h)
            usage
            exit 0
            ;;
        *)
            log_error "未知命令: ${command}"
            echo ""
            usage
            exit 1
            ;;
    esac
}

# 执行主函数
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
