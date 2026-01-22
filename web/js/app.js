/**
 * Docker MCP Gateway - 前端应用
 */

// 全局状态
let startTime = null;
let logRefreshInterval = null;
let currentLogContainer = null;

// DOM 加载完成后初始化
document.addEventListener('DOMContentLoaded', () => {
    initApp();
});

/**
 * 初始化应用
 */
function initApp() {
    // 获取初始状态
    fetchStatus();
    fetchContainers();
    
    // 定时刷新
    setInterval(fetchStatus, 5000);
    setInterval(fetchContainers, 10000);
    setInterval(updateUptime, 1000);
    
    // 初始化模态框
    initAddModal();
    initLogModal();
}

// ==================== 状态获取 ====================

/**
 * 获取网关状态
 */
async function fetchStatus() {
    try {
        const response = await fetch('/api/status');
        const data = await response.json();
        
        // 更新状态徽章
        const statusBadge = document.getElementById('global-status');
        statusBadge.textContent = '在线';
        statusBadge.className = 'status-badge online';
        
        // 设置启动时间
        if (data.start_time) {
            startTime = new Date(data.start_time);
        }
        
        // 更新统计
        document.getElementById('total-containers').textContent = data.total_containers;
        document.getElementById('running-containers').textContent = data.running_containers;
        document.getElementById('total-requests').textContent = formatNumber(data.total_requests);
        
    } catch (error) {
        console.error('获取状态失败:', error);
        const statusBadge = document.getElementById('global-status');
        statusBadge.textContent = '离线';
        statusBadge.className = 'status-badge offline';
    }
}

/**
 * 获取容器列表
 */
async function fetchContainers() {
    try {
        const response = await fetch('/api/containers');
        const containers = await response.json();
        renderContainers(containers);
    } catch (error) {
        console.error('获取容器列表失败:', error);
    }
}

// ==================== 渲染 ====================

/**
 * 渲染容器列表
 */
function renderContainers(containers) {
    const list = document.getElementById('containers-list');
    const emptyState = document.getElementById('empty-state');
    
    if (!containers || containers.length === 0) {
        list.innerHTML = '';
        list.appendChild(emptyState);
        emptyState.style.display = 'block';
        return;
    }
    
    emptyState.style.display = 'none';
    
    const html = containers.map(container => {
        const statusClass = getStatusClass(container.status);
        const externalUrl = `${window.location.origin}${container.external_path}`;
        
        // 端口映射显示
        const portMapping = container.host_port 
            ? `${container.host_port}:${container.internal_port}` 
            : `:${container.internal_port}`;
        
        return `
            <div class="container-item" data-name="${container.name}">
                <div class="container-info">
                    <div class="container-header">
                        <span class="status-indicator ${statusClass}"></span>
                        <h3>${container.name}</h3>
                    </div>
                    <div class="container-url">${externalUrl}</div>
                    <div class="container-meta">
                        <span>📦 ${container.image}</span>
                        <span title="端口映射: 主机端口:容器端口">🚪 ${portMapping}</span>
                        <span>📊 ${formatNumber(container.total_requests)} 请求</span>
                        ${container.memory_mb > 0 ? `<span>💾 ${container.memory_mb.toFixed(1)} MB</span>` : ''}
                        ${container.cpu_percent > 0 ? `<span>⚡ ${container.cpu_percent.toFixed(1)}% CPU</span>` : ''}
                    </div>
                </div>
                <div class="container-actions">
                    <button class="copy-btn" onclick="copyToClipboard('${externalUrl}', this)">复制</button>
                    <button class="btn-small btn-log" onclick="openLogModal('${container.name}')">日志</button>
                    ${container.status === 'running' 
                        ? `<button class="btn-small btn-stop" onclick="stopContainer('${container.name}', this)">停止</button>`
                        : `<button class="btn-small btn-start" onclick="startContainer('${container.name}', this)">启动</button>`
                    }
                    <button class="btn-small btn-delete" onclick="deleteContainer('${container.name}', this)">删除</button>
                </div>
            </div>
        `;
    }).join('');
    
    list.innerHTML = html;
}

/**
 * 获取状态样式类
 */
function getStatusClass(status) {
    const statusMap = {
        'running': 'running',
        'exited': 'exited',
        'stopped': 'stopped',
        'starting': 'starting',
        'created': 'stopped',
        'not_created': 'stopped',
    };
    return statusMap[status] || 'error';
}

/**
 * 更新运行时间
 */
function updateUptime() {
    if (!startTime) return;
    
    const now = new Date();
    const diff = Math.floor((now - startTime) / 1000);
    
    const hours = Math.floor(diff / 3600);
    const minutes = Math.floor((diff % 3600) / 60);
    const seconds = diff % 60;
    
    const pad = (n) => n.toString().padStart(2, '0');
    document.getElementById('uptime').textContent = `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
}

/**
 * 格式化数字
 */
function formatNumber(num) {
    if (num >= 1000000) {
        return (num / 1000000).toFixed(1) + 'M';
    }
    if (num >= 1000) {
        return (num / 1000).toFixed(1) + 'K';
    }
    return num.toString();
}

// ==================== 添加容器模态框 ====================

/**
 * 初始化添加容器模态框
 */
function initAddModal() {
    const modal = document.getElementById('add-modal');
    const addBtn = document.getElementById('add-container-btn');
    const closeBtn = modal.querySelector('.modal-close');
    const cancelBtn = modal.querySelector('.btn-cancel');
    const form = document.getElementById('add-form');
    const textarea = document.getElementById('docker-command');
    
    // 打开模态框
    addBtn.addEventListener('click', () => {
        modal.classList.add('show');
        textarea.focus();
    });
    
    // 关闭模态框
    const closeModal = () => {
        modal.classList.remove('show');
        form.reset();
        document.getElementById('form-error').textContent = '';
        document.getElementById('parse-preview').classList.remove('show');
    };
    
    closeBtn.addEventListener('click', closeModal);
    cancelBtn.addEventListener('click', closeModal);
    
    modal.addEventListener('click', (e) => {
        if (e.target === modal) closeModal();
    });
    
    // 实时解析预览
    let parseTimeout = null;
    textarea.addEventListener('input', () => {
        clearTimeout(parseTimeout);
        parseTimeout = setTimeout(() => {
            parseDockerCommand(textarea.value);
        }, 300);
    });
    
    // 提交表单
    form.addEventListener('submit', handleAddContainer);
}

/**
 * 解析 docker run 命令并显示预览
 */
function parseDockerCommand(command) {
    const preview = document.getElementById('parse-preview');
    const errorDiv = document.getElementById('form-error');
    
    if (!command.trim()) {
        preview.classList.remove('show');
        errorDiv.textContent = '';
        return;
    }
    
    // 简单的客户端解析预览
    try {
        const lines = command.replace(/\\\n/g, ' ').split(/\s+/);
        let name = '';
        let image = '';
        let ports = [];
        let envCount = 0;
        
        for (let i = 0; i < lines.length; i++) {
            const token = lines[i];
            
            if (token === '--name' && lines[i + 1]) {
                name = lines[i + 1];
                i++;
            } else if (token.startsWith('--name=')) {
                name = token.split('=')[1];
            } else if (token === '-p' && lines[i + 1]) {
                ports.push(lines[i + 1]);
                i++;
            } else if (token === '-e' || token === '--env') {
                envCount++;
                i++;
            } else if (!token.startsWith('-') && token.includes('/')) {
                image = token;
            }
        }
        
        if (image) {
            preview.innerHTML = `
                <div class="preview-item"><span class="preview-label">镜像:</span> ${image}</div>
                ${name ? `<div class="preview-item"><span class="preview-label">名称:</span> ${name}</div>` : ''}
                ${ports.length ? `<div class="preview-item"><span class="preview-label">端口:</span> ${ports.join(', ')}</div>` : ''}
                ${envCount ? `<div class="preview-item"><span class="preview-label">环境变量:</span> ${envCount} 个</div>` : ''}
            `;
            preview.classList.add('show');
            errorDiv.textContent = '';
        } else {
            preview.classList.remove('show');
        }
    } catch (e) {
        preview.classList.remove('show');
    }
}

/**
 * 处理添加容器
 */
async function handleAddContainer(e) {
    e.preventDefault();
    
    const submitBtn = e.target.querySelector('.btn-submit');
    const errorDiv = document.getElementById('form-error');
    const command = document.getElementById('docker-command').value.trim();
    
    if (!command) {
        errorDiv.textContent = '请输入 docker run 命令';
        return;
    }
    
    submitBtn.disabled = true;
    submitBtn.textContent = '创建中...';
    errorDiv.textContent = '';
    
    try {
        const response = await fetch('/api/containers', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                docker_command: command,
            }),
        });
        
        const data = await response.json();
        
        if (response.ok) {
            // 成功
            document.getElementById('add-modal').classList.remove('show');
            document.getElementById('add-form').reset();
            document.getElementById('parse-preview').classList.remove('show');
            
            // 刷新列表
            await fetchContainers();
            await fetchStatus();
            
            console.log('容器创建成功:', data);
        } else {
            // 错误
            errorDiv.textContent = data.detail || '创建失败';
        }
    } catch (error) {
        console.error('创建容器失败:', error);
        errorDiv.textContent = '网络错误，请重试';
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = '创建容器';
    }
}

// ==================== 容器操作 ====================

/**
 * 启动容器
 */
async function startContainer(name, btn) {
    const originalText = btn.textContent;
    btn.textContent = '启动中...';
    btn.disabled = true;
    
    try {
        const response = await fetch(`/api/containers/${name}/start`, {
            method: 'POST',
        });
        
        if (response.ok) {
            await fetchContainers();
        } else {
            const data = await response.json();
            alert(`启动失败: ${data.detail}`);
        }
    } catch (error) {
        console.error('启动容器失败:', error);
        alert('网络错误');
    } finally {
        btn.textContent = originalText;
        btn.disabled = false;
    }
}

/**
 * 停止容器
 */
async function stopContainer(name, btn) {
    const originalText = btn.textContent;
    btn.textContent = '停止中...';
    btn.disabled = true;
    
    try {
        const response = await fetch(`/api/containers/${name}/stop`, {
            method: 'POST',
        });
        
        if (response.ok) {
            await fetchContainers();
        } else {
            const data = await response.json();
            alert(`停止失败: ${data.detail}`);
        }
    } catch (error) {
        console.error('停止容器失败:', error);
        alert('网络错误');
    } finally {
        btn.textContent = originalText;
        btn.disabled = false;
    }
}

/**
 * 删除容器
 */
async function deleteContainer(name, btn) {
    if (!confirm(`确定要删除容器 "${name}" 吗？`)) {
        return;
    }
    
    const originalText = btn.textContent;
    btn.textContent = '删除中...';
    btn.disabled = true;
    
    try {
        const response = await fetch(`/api/containers/${name}`, {
            method: 'DELETE',
        });
        
        if (response.ok) {
            await fetchContainers();
            await fetchStatus();
        } else {
            const data = await response.json();
            alert(`删除失败: ${data.detail}`);
        }
    } catch (error) {
        console.error('删除容器失败:', error);
        alert('网络错误');
    } finally {
        btn.textContent = originalText;
        btn.disabled = false;
    }
}

/**
 * 复制到剪贴板
 */
function copyToClipboard(text, btn) {
    navigator.clipboard.writeText(text).then(() => {
        const originalText = btn.textContent;
        btn.textContent = '已复制';
        setTimeout(() => {
            btn.textContent = originalText;
        }, 2000);
    });
}

// ==================== 日志模态框 ====================

/**
 * 初始化日志模态框
 */
function initLogModal() {
    const modal = document.getElementById('log-modal');
    const closeBtn = modal.querySelector('.modal-close');
    const refreshBtn = document.getElementById('log-refresh-btn');
    const autoRefreshCheckbox = document.getElementById('log-auto-refresh');
    
    // 关闭模态框
    closeBtn.addEventListener('click', closeLogModal);
    modal.addEventListener('click', (e) => {
        if (e.target === modal) closeLogModal();
    });
    
    // 刷新按钮
    refreshBtn.addEventListener('click', () => {
        if (currentLogContainer) {
            fetchLogs(currentLogContainer);
        }
    });
    
    // 自动刷新开关
    autoRefreshCheckbox.addEventListener('change', (e) => {
        if (e.target.checked && currentLogContainer) {
            startLogAutoRefresh();
        } else {
            stopLogAutoRefresh();
        }
    });
}

/**
 * 打开日志模态框
 */
function openLogModal(name) {
    currentLogContainer = name;
    document.getElementById('log-container-name').textContent = name;
    document.getElementById('log-modal').classList.add('show');
    
    // 获取日志
    fetchLogs(name);
    
    // 启动自动刷新
    if (document.getElementById('log-auto-refresh').checked) {
        startLogAutoRefresh();
    }
}

/**
 * 关闭日志模态框
 */
function closeLogModal() {
    document.getElementById('log-modal').classList.remove('show');
    stopLogAutoRefresh();
    currentLogContainer = null;
}

/**
 * 获取日志
 */
async function fetchLogs(name) {
    try {
        const response = await fetch(`/api/containers/${name}/logs?tail=200`);
        const data = await response.json();
        
        const logContent = document.getElementById('log-content');
        logContent.textContent = data.logs || '暂无日志';
        
        // 滚动到底部
        const logContainer = document.getElementById('log-container');
        logContainer.scrollTop = logContainer.scrollHeight;
    } catch (error) {
        console.error('获取日志失败:', error);
        document.getElementById('log-content').textContent = '获取日志失败';
    }
}

/**
 * 启动日志自动刷新
 */
function startLogAutoRefresh() {
    stopLogAutoRefresh();
    if (currentLogContainer) {
        logRefreshInterval = setInterval(() => {
            fetchLogs(currentLogContainer);
        }, 2000);
    }
}

/**
 * 停止日志自动刷新
 */
function stopLogAutoRefresh() {
    if (logRefreshInterval) {
        clearInterval(logRefreshInterval);
        logRefreshInterval = null;
    }
}
