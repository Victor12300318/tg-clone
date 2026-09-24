// Telegram Channel Cloner & Publisher Pro - Alpine.js & Carmine Red Theme Logic

async function apiFetch(path, opts = {}) {
    const token = localStorage.getItem('tg_token');
    opts.headers = Object.assign({}, opts.headers || {}, token ? { 'Authorization': 'Bearer ' + token } : {});
    const res = await fetch(path, opts);
    if (res.status === 401) {
        localStorage.removeItem('tg_token');
        window.location.reload();
    }
    return res;
}

document.addEventListener('alpine:init', () => {
    Alpine.data('app', () => ({
        // Session State
        showLogin: false,
        loginMode: 'login', // 'login' | 'register'
        loginForm: { email: '', password: '' },
        loginLoading: false,
        sessionEmail: localStorage.getItem('tg_email') || '',
        syncingTaskId: null,

        // Global UI State
        theme: localStorage.getItem('tg_theme') || 'dark',
        activeTab: 'tasks', // 'tasks', 'new-task', 'publisher', 'groups', 'rules', 'logs'
        mobileDrawerOpen: false,
        
        // Auth State
        auth: {
            is_authenticated: false,
            account_type: null,
            account_name: null,
            phone_number: null,
            user_id: null,
            username: null
        },
        authModalOpen: false,
        authMode: 'user', // 'user' | 'bot'
        authStep: 1, // 1: credentials & phone/token, 2: code, 3: 2fa password
        authLoading: false,
        authForm: {
            phone_number: localStorage.getItem('tg_phone') || '',
            phone_code: '',
            phone_code_hash: '',
            password: '',
            bot_token: ''
        },

        // Telegram Dialogs (Chats / Channels / Groups from Account)
        dialogs: [],
        loadingDialogs: false,
        dialogsSearchOrigin: '',
        dialogsSearchDest: '',
        manualOriginInput: false,
        manualDestInput: false,

        // Managed Groups Registry State
        managedGroups: [],
        groupsSearch: '',
        importModalOpen: false,
        importSearch: '',
        selectedDialogsToImport: [],
        importingGroups: false,

        // Publisher State
        posts: [],
        postsSearch: '',
        publisherTab: 'list', // 'list' | 'composer'
        composerStep: 1, // 1: Conteúdo, 2: Destinos, 3: Agendamento & Revisão
        isEditingPost: false,
        editingPostId: null,
        uploadingMedia: false,
        postForm: {
            name: '',
            text: '',
            media_path: null,
            media_filename: null,
            media_type: null,
            target_group_ids: [],
            schedule_type: 'now', // 'now' | 'once' | 'recurring'
            run_at: '',
            recurrence_rule: {
                freq: 'daily',
                interval_hours: 4,
                weekday: 0,
                time_hhmm: '09:00'
            }
        },
        bulkEditModal: {
            open: false,
            postId: null,
            postName: '',
            newText: '',
            loading: false
        },
        bulkDeleteModal: {
            open: false,
            postId: null,
            postName: '',
            loading: false
        },
        deliveriesModal: {
            open: false,
            postId: null,
            postName: '',
            deliveries: [],
            loading: false
        },

        // Cloner Tasks State & Filters
        tasks: [],
        taskFilter: 'all', // 'all', 'running', 'paused', 'completed'
        taskSearch: '',
        expandedTaskLogs: {}, // { [taskId]: boolean }
        deleteModal: {
            open: false,
            taskId: null,
            taskName: ''
        },

        // Wizard State for Cloner New / Edit Task
        wizardStep: 1, // 1: Objetivo/Modo, 2: Origem & Destino, 3: Conteúdo & Limpeza, 4: Revisar & Criar
        isEditingTask: false,
        editingTaskId: null,
        autoStartOnCreate: true,
        delayPreset: 'normal', // 'slow' (20s), 'normal' (10s), 'fast' (3s), 'custom'
        mediaPreset: 'all', // 'all', 'media_only', 'text_only', 'custom'
        showAdvancedOptions: false,
        taskForm: {
            name: 'Clonagem de Canal',
            mode: 'historical',
            origin_chat: '',
            dest_chat: '',
            start_message_id: 1,
            end_message_id: null,
            media_types: ['all'],
            clean_forward: true,
            delay_seconds: 10.0,
            skip_delay_seconds: 0.5,
            remove_captions: false,
            remove_links: false,
            remove_mentions: false,
            header_text: '',
            footer_text: '',
            custom_replacements: []
        },
        newRuleInTask: {
            pattern: '',
            replacement: '',
            is_regex: false,
            enabled: true
        },
        taskOriginCheck: { loading: false, title: null, error: null },
        taskDestCheck: { loading: false, title: null, error: null },

        // Global Rules State
        rules: [],
        newGlobalRule: {
            name: '',
            pattern: '',
            replacement: '',
            is_regex: false,
            enabled: true
        },

        // Logs & Terminal State
        logs: [],
        logFilter: 'all',
        logSearch: '',
        autoScrollLogs: true,

        // WebSocket
        ws: null,
        wsConnected: false,

        // Toasts
        toasts: [],

        // Lifecycle init
        async init() {
            this.applyTheme();
            if (!localStorage.getItem('tg_token')) {
                this.showLogin = true;
                this.refreshIcons();
                return;
            }
            await this.fetchAuthStatus();
            await this.fetchTasks();
            await this.fetchRules();
            await this.fetchManagedGroups();
            await this.fetchPosts();
            this.initWebSocket();
            this.refreshIcons();

            // Periodic polling fallback (every 8s)
            setInterval(() => {
                this.fetchTasks(false);
                this.fetchPosts();
            }, 8000);

            // Re-render icons on tab or modal changes
            this.$watch('activeTab', () => this.refreshIcons());
            this.$watch('publisherTab', () => this.refreshIcons());
            this.$watch('wizardStep', () => this.refreshIcons());
            this.$watch('composerStep', () => this.refreshIcons());
            this.$watch('authModalOpen', () => this.refreshIcons());
            this.$watch('importModalOpen', () => this.refreshIcons());
            this.$watch('deleteModal.open', () => this.refreshIcons());
            this.$watch('bulkEditModal.open', () => this.refreshIcons());
            this.$watch('bulkDeleteModal.open', () => this.refreshIcons());
            this.$watch('deliveriesModal.open', () => this.refreshIcons());
            this.$watch('tasks', () => this.refreshIcons());
            this.$watch('posts', () => this.refreshIcons());
            this.$watch('managedGroups', () => this.refreshIcons());
        },

        refreshIcons() {
            this.$nextTick(() => {
                if (window.lucide && typeof window.lucide.createIcons === 'function') {
                    window.lucide.createIcons();
                }
            });
        },

        applyTheme() {
            if (this.theme === 'dark') {
                document.documentElement.classList.add('dark');
            } else {
                document.documentElement.classList.remove('dark');
            }
            localStorage.setItem('tg_theme', this.theme);
            this.refreshIcons();
        },

        toggleTheme() {
            this.theme = this.theme === 'dark' ? 'light' : 'dark';
            this.applyTheme();
        },

        showToast(message, type = 'info') {
            const id = Date.now();
            this.toasts.push({ id, message, type });
            this.refreshIcons();
            setTimeout(() => {
                this.toasts = this.toasts.filter(t => t.id !== id);
            }, 4000);
        },

        // WebSocket Connection
        initWebSocket() {
            const token = localStorage.getItem('tg_token');
            if (!token) return;
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/ws?token=${encodeURIComponent(token)}`;

            try {
                this.ws = new WebSocket(wsUrl);

                this.ws.onopen = () => {
                    this.wsConnected = true;
                    this.refreshIcons();
                };

                this.ws.onmessage = (event) => {
                    try {
                        const payload = JSON.parse(event.data);
                        this.handleWsEvent(payload);
                    } catch (e) {
                        console.error('Error parsing WS message', e);
                    }
                };

                this.ws.onclose = () => {
                    this.wsConnected = false;
                    this.refreshIcons();
                    setTimeout(() => this.initWebSocket(), 3000);
                };

                this.ws.onerror = () => {
                    this.wsConnected = false;
                    this.refreshIcons();
                };
            } catch (e) {
                console.error('WS Init error:', e);
            }
        },

        handleWsEvent(payload) {
            const { event, data, timestamp } = payload;

            if (event === 'log' || event === 'publisher_log') {
                this.logs.push({
                    task_id: data.task_id,
                    level: data.level || 'info',
                    message: data.message,
                    timestamp: timestamp || new Date().toLocaleTimeString()
                });
                if (this.logs.length > 300) this.logs.shift();
                if (this.autoScrollLogs) {
                    this.$nextTick(() => {
                        const terminal = document.getElementById('terminal-container');
                        if (terminal) terminal.scrollTop = terminal.scrollHeight;
                    });
                }
            } else if (event === 'history_logs') {
                this.logs = data || [];
                if (this.autoScrollLogs) {
                    this.$nextTick(() => {
                        const terminal = document.getElementById('terminal-container');
                        if (terminal) terminal.scrollTop = terminal.scrollHeight;
                    });
                }
            } else if (event === 'task_progress') {
                const targetTask = this.tasks.find(t => t.id === data.task_id);
                if (targetTask) {
                    targetTask.current_message_id = data.current;
                    targetTask.total_messages = data.total;
                    targetTask.copied_count = data.copied;
                    targetTask.skipped_count = data.skipped;
                    targetTask.error_count = data.errors;
                    targetTask.processed_messages = data.copied + data.skipped + data.errors;
                }
            } else if (event === 'task_status') {
                const targetTask = this.tasks.find(t => t.id === data.task_id);
                const prevStatus = targetTask ? targetTask.status : null;
                if (targetTask) {
                    targetTask.status = data.status;
                }
                if (prevStatus === 'running' && data.status === 'completed') {
                    this.showToast(`Tarefa "${targetTask?.name || ''}" foi concluída com sucesso!`, 'success');
                } else if (prevStatus === 'running' && data.status === 'failed') {
                    this.showToast(`Tarefa "${targetTask?.name || ''}" foi interrompida com falha.`, 'error');
                }
                this.fetchTasks(false);
            } else if (event === 'post_updated') {
                this.fetchPosts();
            }
        },

        // Session Operations
        async submitLogin() {
            const email = this.loginForm.email.trim().toLowerCase();
            if (!email || !this.loginForm.password) {
                this.showToast('Preencha email e senha.', 'warning');
                return;
            }
            this.loginLoading = true;
            try {
                const res = await fetch(this.loginMode === 'login' ? '/api/users/login' : '/api/users/register', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email, password: this.loginForm.password })
                });
                const data = await res.json();
                if (res.ok && data.token) {
                    localStorage.setItem('tg_token', data.token);
                    localStorage.setItem('tg_email', data.email);
                    window.location.reload();
                } else {
                    this.showToast(data.detail || 'Falha na autenticação.', 'error');
                }
            } catch (e) {
                this.showToast('Erro de conexão.', 'error');
            } finally {
                this.loginLoading = false;
            }
        },

        logoutSession() {
            localStorage.removeItem('tg_token');
            localStorage.removeItem('tg_email');
            window.location.reload();
        },

        // Auth Operations
        async fetchAuthStatus() {
            try {
                const res = await apiFetch('/api/auth/status');
                if (res.ok) {
                    this.auth = await res.json();
                    if (this.auth.is_authenticated) {
                        this.fetchDialogs();
                    }
                }
            } catch (e) {
                console.error('Fetch auth status error', e);
            } finally {
                this.refreshIcons();
            }
        },

        openAuthModal() {
            this.authStep = 1;
            this.authModalOpen = true;
            this.refreshIcons();
        },

        async sendCode() {
            if (!this.authForm.phone_number) {
                this.showToast('Preencha o número de telefone.', 'warning');
                return;
            }

            this.authLoading = true;
            localStorage.setItem('tg_phone', this.authForm.phone_number);

            try {
                const res = await apiFetch('/api/auth/send-code', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        phone_number: this.authForm.phone_number.trim()
                    })
                });
                const data = await res.json();
                if (res.ok && data.success) {
                    this.authForm.phone_code_hash = data.phone_code_hash;
                    this.authStep = 2;
                    this.showToast('Código enviado para seu aplicativo do Telegram.', 'success');
                } else {
                    this.showToast(data.detail || data.error || 'Erro ao enviar código.', 'error');
                }
            } catch (e) {
                this.showToast('Erro de conexão ao enviar código.', 'error');
            } finally {
                this.authLoading = false;
                this.refreshIcons();
            }
        },

        async verifyCode() {
            if (!this.authForm.phone_code) {
                this.showToast('Digite o código de verificação recebido.', 'warning');
                return;
            }

            this.authLoading = true;
            try {
                const res = await apiFetch('/api/auth/verify-code', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        phone_number: this.authForm.phone_number.trim(),
                        phone_code_hash: this.authForm.phone_code_hash,
                        phone_code: this.authForm.phone_code.trim()
                    })
                });
                const data = await res.json();
                if (res.ok && data.success) {
                    if (data.requires_2fa) {
                        this.authStep = 3;
                        this.showToast('Autenticação de 2 etapas (2FA) necessária.', 'info');
                    } else {
                        this.showToast(data.message || 'Conectado com sucesso!', 'success');
                        this.authModalOpen = false;
                        await this.fetchAuthStatus();
                    }
                } else {
                    this.showToast(data.detail || data.error || 'Código incorreto.', 'error');
                }
            } catch (e) {
                this.showToast('Erro ao validar código.', 'error');
            } finally {
                this.authLoading = false;
                this.refreshIcons();
            }
        },

        async verifyPassword() {
            if (!this.authForm.password) {
                this.showToast('Digite sua senha 2FA.', 'warning');
                return;
            }

            this.authLoading = true;
            try {
                const res = await apiFetch('/api/auth/verify-password', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        phone_number: this.authForm.phone_number.trim(),
                        password: this.authForm.password
                    })
                });
                const data = await res.json();
                if (res.ok && data.success) {
                    this.showToast(data.message || 'Conectado com sucesso!', 'success');
                    this.authModalOpen = false;
                    await this.fetchAuthStatus();
                } else {
                    this.showToast(data.detail || data.error || 'Senha incorreta.', 'error');
                }
            } catch (e) {
                this.showToast('Erro ao validar senha 2FA.', 'error');
            } finally {
                this.authLoading = false;
                this.refreshIcons();
            }
        },

        async botLogin() {
            if (!this.authForm.bot_token) {
                this.showToast('Preencha o Bot Token.', 'warning');
                return;
            }

            this.authLoading = true;
            try {
                const res = await apiFetch('/api/auth/bot-login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        bot_token: this.authForm.bot_token.trim()
                    })
                });
                const data = await res.json();
                if (res.ok && data.success) {
                    this.showToast(data.message || 'Bot conectado!', 'success');
                    this.authModalOpen = false;
                    await this.fetchAuthStatus();
                } else {
                    this.showToast(data.detail || data.error || 'Erro ao conectar Bot.', 'error');
                }
            } catch (e) {
                this.showToast('Erro ao conectar Bot.', 'error');
            } finally {
                this.authLoading = false;
                this.refreshIcons();
            }
        },

        async logout() {
            try {
                await apiFetch('/api/auth/logout', { method: 'POST' });
                this.auth.is_authenticated = false;
                this.dialogs = [];
                this.showToast('Desconectado com sucesso.', 'info');
            } catch (e) {
                console.error(e);
            } finally {
                this.refreshIcons();
            }
        },

        async fetchDialogs() {
            this.loadingDialogs = true;
            try {
                const res = await apiFetch('/api/auth/dialogs');
                if (res.ok) {
                    const data = await res.json();
                    this.dialogs = Array.isArray(data) ? data : (data.dialogs || []);
                }
            } catch (e) {
                console.error('Fetch dialogs error', e);
            } finally {
                this.loadingDialogs = false;
                this.refreshIcons();
            }
        },

        getFilteredDialogs(query = '') {
            if (!query.trim()) return this.dialogs;
            const q = query.toLowerCase();
            return this.dialogs.filter(d => 
                (d.title && d.title.toLowerCase().includes(q)) ||
                (d.username && d.username.toLowerCase().includes(q)) ||
                (d.id && String(d.id).includes(q))
            );
        },

        // ==========================================
        // MANAGED GROUPS OPERATIONS
        // ==========================================
        async fetchManagedGroups() {
            try {
                const res = await apiFetch('/api/groups');
                if (res.ok) {
                    this.managedGroups = await res.json();
                }
            } catch (e) {
                console.error('Fetch groups error', e);
            } finally {
                this.refreshIcons();
            }
        },

        get filteredManagedGroups() {
            if (!this.groupsSearch.trim()) return this.managedGroups;
            const q = this.groupsSearch.toLowerCase();
            return this.managedGroups.filter(g => 
                (g.title && g.title.toLowerCase().includes(q)) ||
                (g.chat_id && g.chat_id.includes(q))
            );
        },

        openImportGroupsModal() {
            this.selectedDialogsToImport = [];
            this.importSearch = '';
            this.importModalOpen = true;
            this.refreshIcons();
        },

        toggleDialogToImport(dialog) {
            const id = String(dialog.id);
            if (this.selectedDialogsToImport.some(d => String(d.id) === id)) {
                this.selectedDialogsToImport = this.selectedDialogsToImport.filter(d => String(d.id) !== id);
            } else {
                this.selectedDialogsToImport.push(dialog);
            }
        },

        isDialogSelectedToImport(dialog) {
            return this.selectedDialogsToImport.some(d => String(d.id) === String(dialog.id));
        },

        selectAllImportableDialogs() {
            const filtered = this.getFilteredDialogs(this.importSearch);
            this.selectedDialogsToImport = [...filtered];
        },

        deselectAllImportableDialogs() {
            this.selectedDialogsToImport = [];
        },

        async importSelectedGroups() {
            if (this.selectedDialogsToImport.length === 0) {
                this.showToast('Selecione ao menos um grupo para importar.', 'warning');
                return;
            }
            this.importingGroups = true;
            try {
                const payload = this.selectedDialogsToImport.map(d => ({
                    chat_id: String(d.id),
                    title: d.title || `Chat ${d.id}`,
                    chat_type: d.type || 'supergroup',
                    is_admin: true
                }));
                const res = await apiFetch('/api/groups/bulk', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                if (res.ok) {
                    this.showToast(`${this.selectedDialogsToImport.length} grupos importados com sucesso!`, 'success');
                    this.importModalOpen = false;
                    this.selectedDialogsToImport = [];
                    await this.fetchManagedGroups();
                } else {
                    this.showToast('Erro ao importar grupos.', 'error');
                }
            } catch (e) {
                this.showToast('Erro de conexão ao importar grupos.', 'error');
            } finally {
                this.importingGroups = false;
                this.refreshIcons();
            }
        },

        async removeManagedGroup(id) {
            try {
                const res = await apiFetch(`/api/groups/${id}`, { method: 'DELETE' });
                if (res.ok) {
                    this.showToast('Grupo removido do registro.', 'info');
                    await this.fetchManagedGroups();
                }
            } catch (e) {
                this.showToast('Erro ao remover grupo.', 'error');
            } finally {
                this.refreshIcons();
            }
        },

        // ==========================================
        // PUBLISHER OPERATIONS
        // ==========================================
        async fetchPosts() {
            try {
                const res = await apiFetch('/api/publisher/posts');
                if (res.ok) {
                    this.posts = await res.json();
                }
            } catch (e) {
                console.error('Fetch posts error', e);
            } finally {
                this.refreshIcons();
            }
        },

        get filteredPosts() {
            if (!this.postsSearch.trim()) return this.posts;
            const q = this.postsSearch.toLowerCase();
            return this.posts.filter(p => 
                (p.name && p.name.toLowerCase().includes(q)) ||
                (p.text && p.text.toLowerCase().includes(q))
            );
        },

        openNewPostComposer() {
            this.isEditingPost = false;
            this.editingPostId = null;
            this.composerStep = 1;
            this.postForm = {
                name: 'Postagem ' + (this.posts.length + 1),
                text: '',
                media_path: null,
                media_filename: null,
                media_type: null,
                target_group_ids: this.managedGroups.map(g => g.id), // Default select all
                schedule_type: 'now',
                run_at: '',
                recurrence_rule: {
                    freq: 'daily',
                    interval_hours: 4,
                    weekday: 0,
                    time_hhmm: '09:00'
                }
            };
            this.publisherTab = 'composer';
            this.refreshIcons();
        },

        editPostContent(post) {
            this.isEditingPost = true;
            this.editingPostId = post.id;
            this.composerStep = 1;
            this.postForm = {
                name: post.name,
                text: post.text,
                media_path: post.media_path,
                media_filename: post.media_path ? post.media_path.split(/[\\/]/).pop() : null,
                media_type: post.media_type,
                target_group_ids: post.target_group_ids || [],
                schedule_type: post.schedule_type,
                run_at: post.run_at || '',
                recurrence_rule: post.recurrence_rule ? JSON.parse(JSON.stringify(post.recurrence_rule)) : {
                    freq: 'daily',
                    interval_hours: 4,
                    weekday: 0,
                    time_hhmm: '09:00'
                }
            };
            this.publisherTab = 'composer';
            this.refreshIcons();
        },

        async handleMediaFileUpload(event) {
            const file = event.target.files[0];
            if (!file) return;

            this.uploadingMedia = true;
            const formData = new FormData();
            formData.append('file', file);

            try {
                const res = await apiFetch('/api/publisher/uploads', {
                    method: 'POST',
                    body: formData
                });
                const data = await res.json();
                if (res.ok && data.success) {
                    this.postForm.media_path = data.file_path;
                    this.postForm.media_filename = data.filename;
                    this.postForm.media_type = data.media_type;
                    this.showToast('Mídia carregada com sucesso!', 'success');
                } else {
                    this.showToast(data.detail || 'Erro ao enviar mídia.', 'error');
                }
            } catch (e) {
                this.showToast('Erro de conexão ao carregar arquivo.', 'error');
            } finally {
                this.uploadingMedia = false;
                this.refreshIcons();
            }
        },

        removeMediaFromPost() {
            this.postForm.media_path = null;
            this.postForm.media_filename = null;
            this.postForm.media_type = null;
            this.refreshIcons();
        },

        togglePostTargetGroup(groupId) {
            if (this.postForm.target_group_ids.includes(groupId)) {
                this.postForm.target_group_ids = this.postForm.target_group_ids.filter(id => id !== groupId);
            } else {
                this.postForm.target_group_ids.push(groupId);
            }
        },

        isTargetGroupSelected(groupId) {
            return this.postForm.target_group_ids.includes(groupId);
        },

        selectAllTargetGroups() {
            this.postForm.target_group_ids = this.managedGroups.map(g => g.id);
        },

        deselectAllTargetGroups() {
            this.postForm.target_group_ids = [];
        },

        canAdvanceComposerStep(step) {
            if (step === 1) {
                if (!this.postForm.name.trim()) {
                    this.showToast('Informe um nome para a postagem.', 'warning');
                    return false;
                }
                if (!this.postForm.text.trim() && !this.postForm.media_path) {
                    this.showToast('Escreva um texto ou anexe uma mídia para o post.', 'warning');
                    return false;
                }
                return true;
            }
            if (step === 2) {
                if (this.postForm.target_group_ids.length === 0) {
                    this.showToast('Selecione ao menos um grupo de destino.', 'warning');
                    return false;
                }
                return true;
            }
            return true;
        },

        nextComposerStep() {
            if (this.canAdvanceComposerStep(this.composerStep)) {
                if (this.composerStep < 3) {
                    this.composerStep++;
                    this.refreshIcons();
                }
            }
        },

        prevComposerStep() {
            if (this.composerStep > 1) {
                this.composerStep--;
                this.refreshIcons();
            }
        },

        getPostSummaryText() {
            const totalGroups = this.postForm.target_group_ids.length;
            const mediaText = this.postForm.media_type ? `com 1 ${this.postForm.media_type}` : 'apenas texto';
            let schedText = 'envio imediato';
            if (this.postForm.schedule_type === 'once') {
                schedText = `agendado para ${this.postForm.run_at || 'data configurada'}`;
            } else if (this.postForm.schedule_type === 'recurring') {
                schedText = `recorrente (${this.postForm.recurrence_rule.freq})`;
            }
            return `Publicar ${mediaText} para ${totalGroups} grupo(s) cadastrado(s) (${schedText}).`;
        },

        async submitPostForm() {
            try {
                const payload = {
                    name: this.postForm.name.trim(),
                    text: this.postForm.text.trim(),
                    media_path: this.postForm.media_path,
                    media_type: this.postForm.media_type,
                    target_group_ids: this.postForm.target_group_ids,
                    schedule_type: this.postForm.schedule_type,
                    run_at: this.postForm.schedule_type === 'once' ? this.postForm.run_at : null,
                    recurrence_rule: this.postForm.schedule_type === 'recurring' ? this.postForm.recurrence_rule : null
                };

                let res, createdOrUpdated;
                if (this.isEditingPost && this.editingPostId) {
                    res = await apiFetch(`/api/publisher/posts/${this.editingPostId}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    if (res.ok) {
                        createdOrUpdated = await res.json();
                        this.showToast(`Postagem "${createdOrUpdated.name}" atualizada!`, 'success');
                    }
                } else {
                    res = await apiFetch('/api/publisher/posts', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    if (res.ok) {
                        createdOrUpdated = await res.json();
                        this.showToast(`Postagem "${createdOrUpdated.name}" salva com sucesso!`, 'success');
                        
                        // If immediate send chosen, trigger publish-now
                        if (this.postForm.schedule_type === 'now' && createdOrUpdated.id) {
                            await this.publishPostNow(createdOrUpdated.id);
                        }
                    }
                }

                if (res.ok) {
                    await this.fetchPosts();
                    this.publisherTab = 'list';
                } else {
                    const err = await res.json();
                    this.showToast(err.detail || 'Erro ao salvar postagem.', 'error');
                }
            } catch (e) {
                this.showToast('Erro de conexão ao salvar postagem.', 'error');
            } finally {
                this.refreshIcons();
            }
        },

        async publishPostNow(postId) {
            try {
                const res = await apiFetch(`/api/publisher/posts/${postId}/publish-now`, { method: 'POST' });
                const data = await res.json();
                if (res.ok && data.success) {
                    this.showToast('Disparo iniciado em segundo plano!', 'success');
                    await this.fetchPosts();
                } else {
                    this.showToast(data.detail || 'Erro ao iniciar disparo.', 'error');
                }
            } catch (e) {
                this.showToast('Erro ao comunicar com o servidor.', 'error');
            } finally {
                this.refreshIcons();
            }
        },

        openBulkEditModal(post) {
            this.bulkEditModal = {
                open: true,
                postId: post.id,
                postName: post.name,
                newText: post.text,
                loading: false
            };
            this.refreshIcons();
        },

        async executeBulkEdit() {
            if (!this.bulkEditModal.newText.trim()) {
                this.showToast('Digite o novo texto.', 'warning');
                return;
            }
            this.bulkEditModal.loading = true;
            try {
                const res = await apiFetch(`/api/publisher/posts/${this.bulkEditModal.postId}/bulk-edit`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ new_text: this.bulkEditModal.newText.trim() })
                });
                const data = await res.json();
                if (res.ok && data.success) {
                    this.showToast(`Texto atualizado em ${data.edited_count} grupo(s)!`, 'success');
                    this.bulkEditModal.open = false;
                    await this.fetchPosts();
                } else {
                    this.showToast(data.detail || 'Erro na edição em massa.', 'error');
                }
            } catch (e) {
                this.showToast('Erro de conexão ao editar.', 'error');
            } finally {
                this.bulkEditModal.loading = false;
                this.refreshIcons();
            }
        },

        openBulkDeleteModal(post) {
            this.bulkDeleteModal = {
                open: true,
                postId: post.id,
                postName: post.name,
                loading: false
            };
            this.refreshIcons();
        },

        async executeBulkDelete() {
            this.bulkDeleteModal.loading = true;
            try {
                const res = await apiFetch(`/api/publisher/posts/${this.bulkDeleteModal.postId}/bulk-delete`, {
                    method: 'POST'
                });
                const data = await res.json();
                if (res.ok && data.success) {
                    this.showToast(`Mensagens apagadas em ${data.deleted_count} grupo(s)!`, 'info');
                    this.bulkDeleteModal.open = false;
                    await this.fetchPosts();
                } else {
                    this.showToast(data.detail || 'Erro ao apagar mensagens.', 'error');
                }
            } catch (e) {
                this.showToast('Erro de conexão ao apagar.', 'error');
            } finally {
                this.bulkDeleteModal.loading = false;
                this.refreshIcons();
            }
        },

        async viewPostDeliveries(post) {
            this.deliveriesModal = {
                open: true,
                postId: post.id,
                postName: post.name,
                deliveries: [],
                loading: true
            };
            this.refreshIcons();

            try {
                const res = await apiFetch(`/api/publisher/posts/${post.id}/deliveries`);
                if (res.ok) {
                    this.deliveriesModal.deliveries = await res.json();
                }
            } catch (e) {
                console.error(e);
            } finally {
                this.deliveriesModal.loading = false;
                this.refreshIcons();
            }
        },

        async deletePostRecord(postId) {
            try {
                const res = await apiFetch(`/api/publisher/posts/${postId}`, { method: 'DELETE' });
                if (res.ok) {
                    this.showToast('Postagem removida.', 'info');
                    await this.fetchPosts();
                }
            } catch (e) {
                this.showToast('Erro ao remover postagem.', 'error');
            } finally {
                this.refreshIcons();
            }
        },

        getPostStatusLabel(status) {
            const map = {
                draft: 'Rascunho',
                scheduled: 'Agendado',
                publishing: 'Publicando',
                published: 'Publicado',
                partially_failed: 'Parcial',
                failed: 'Falha',
                cancelled: 'Cancelado'
            };
            return map[status] || status;
        },

        getPostStatusBadgeClass(status) {
            if (status === 'published') return 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300 border border-gray-300 dark:border-gray-700';
            if (status === 'publishing') return 'bg-rose-500/15 text-rose-600 dark:text-rose-400 border border-rose-500/30 pulse-active';
            if (status === 'scheduled') return 'bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/20';
            if (status === 'draft') return 'bg-gray-50 text-gray-500 dark:bg-gray-900 dark:text-gray-400 border border-gray-200 dark:border-gray-800';
            return 'bg-red-950/40 text-red-400 border border-red-800/40';
        },

        // ==========================================
        // CLONER TASKS OPERATIONS
        // ==========================================
        selectOriginDialog(dialog) {
            this.taskForm.origin_chat = String(dialog.id);
            this.taskOriginCheck.title = dialog.title;
            this.taskOriginCheck.error = null;
            this.refreshIcons();
        },

        selectDestDialog(dialog) {
            this.taskForm.dest_chat = String(dialog.id);
            this.taskDestCheck.title = dialog.title;
            this.taskDestCheck.error = null;
            this.refreshIcons();
        },

        getOriginDisplayName() {
            if (this.taskOriginCheck.title) return this.taskOriginCheck.title;
            const found = this.dialogs.find(d => String(d.id) === String(this.taskForm.origin_chat));
            return found ? found.title : (this.taskForm.origin_chat || 'Não selecionado');
        },

        getDestDisplayName() {
            if (this.taskDestCheck.title) return this.taskDestCheck.title;
            const found = this.dialogs.find(d => String(d.id) === String(this.taskForm.dest_chat));
            return found ? found.title : (this.taskForm.dest_chat || 'Não selecionado');
        },

        async checkOriginChat() {
            if (!this.taskForm.origin_chat) return;
            this.taskOriginCheck.loading = true;
            this.taskOriginCheck.title = null;
            this.taskOriginCheck.error = null;

            try {
                const res = await apiFetch('/api/auth/check-chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ chat_identifier: this.taskForm.origin_chat })
                });
                const data = await res.json();
                if (data.valid) {
                    this.taskOriginCheck.title = data.title;
                } else {
                    this.taskOriginCheck.error = data.error || 'Chat não acessível';
                }
            } catch (e) {
                this.taskOriginCheck.error = 'Erro na validação';
            } finally {
                this.taskOriginCheck.loading = false;
                this.refreshIcons();
            }
        },

        async checkDestChat() {
            if (!this.taskForm.dest_chat) return;
            this.taskDestCheck.loading = true;
            this.taskDestCheck.title = null;
            this.taskDestCheck.error = null;

            try {
                const res = await apiFetch('/api/auth/check-chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ chat_identifier: this.taskForm.dest_chat })
                });
                const data = await res.json();
                if (data.valid) {
                    this.taskDestCheck.title = data.title;
                } else {
                    this.taskDestCheck.error = data.error || 'Chat não acessível';
                }
            } catch (e) {
                this.taskDestCheck.error = 'Erro na validação';
            } finally {
                this.taskDestCheck.loading = false;
                this.refreshIcons();
            }
        },

        // Wizard Step Management
        openNewTaskWizard() {
            this.isEditingTask = false;
            this.editingTaskId = null;
            this.wizardStep = 1;
            this.showAdvancedOptions = false;
            this.delayPreset = 'normal';
            this.mediaPreset = 'all';
            this.taskOriginCheck = { loading: false, title: null, error: null };
            this.taskDestCheck = { loading: false, title: null, error: null };
            this.taskForm = {
                name: 'Clonagem de Canal ' + (this.tasks.length + 1),
                mode: 'historical',
                origin_chat: '',
                dest_chat: '',
                start_message_id: 1,
                end_message_id: null,
                media_types: ['all'],
                clean_forward: true,
                delay_seconds: 10.0,
                skip_delay_seconds: 0.5,
                remove_captions: false,
                remove_links: false,
                remove_mentions: false,
                header_text: '',
                footer_text: '',
                custom_replacements: []
            };
            this.activeTab = 'new-task';
            this.mobileDrawerOpen = false;
            if (this.dialogs.length === 0 && this.auth && this.auth.is_authenticated) {
                this.fetchDialogs();
            }
            this.refreshIcons();
        },

        goToWizardStep(step) {
            if (step > this.wizardStep && !this.canAdvanceStep(this.wizardStep)) {
                return;
            }
            this.wizardStep = step;
            if (step === 2 && this.dialogs.length === 0 && this.auth && this.auth.is_authenticated) {
                this.fetchDialogs();
            }
            this.refreshIcons();
        },

        nextWizardStep() {
            if (this.canAdvanceStep(this.wizardStep)) {
                if (this.wizardStep < 4) {
                    this.wizardStep++;
                    if (this.wizardStep === 2 && this.dialogs.length === 0 && this.auth && this.auth.is_authenticated) {
                        this.fetchDialogs();
                    }
                    this.refreshIcons();
                }
            }
        },

        prevWizardStep() {
            if (this.wizardStep > 1) {
                this.wizardStep--;
                this.refreshIcons();
            }
        },

        canAdvanceStep(step) {
            if (step === 1) {
                if (!this.taskForm.name.trim()) {
                    this.showToast('Dê um nome para a tarefa.', 'warning');
                    return false;
                }
                return true;
            }
            if (step === 2) {
                if (!this.taskForm.origin_chat) {
                    this.showToast('Selecione o canal ou grupo de Origem.', 'warning');
                    return false;
                }
                if (!this.taskForm.dest_chat) {
                    this.showToast('Selecione o canal ou grupo de Destino.', 'warning');
                    return false;
                }
                if (String(this.taskForm.origin_chat).trim() === String(this.taskForm.dest_chat).trim()) {
                    this.showToast('Origem e Destino não podem ser o mesmo canal.', 'warning');
                    return false;
                }
                return true;
            }
            if (step === 3) {
                if (!this.taskForm.media_types || this.taskForm.media_types.length === 0) {
                    this.showToast('Selecione ao menos um tipo de mídia.', 'warning');
                    return false;
                }
                return true;
            }
            return true;
        },

        // Media Presets
        applyMediaPreset(preset) {
            this.mediaPreset = preset;
            if (preset === 'all') {
                this.taskForm.media_types = ['all'];
            } else if (preset === 'media_only') {
                this.taskForm.media_types = ['photo', 'video', 'document', 'audio', 'voice', 'animation'];
            } else if (preset === 'text_only') {
                this.taskForm.media_types = ['text'];
            }
            this.refreshIcons();
        },

        toggleMediaType(type) {
            if (type === 'all') {
                this.taskForm.media_types = ['all'];
                this.mediaPreset = 'all';
                this.refreshIcons();
                return;
            }

            let types = this.taskForm.media_types.filter(t => t !== 'all');
            if (types.includes(type)) {
                types = types.filter(t => t !== type);
            } else {
                types.push(type);
            }

            if (types.length === 0) {
                types = ['all'];
                this.mediaPreset = 'all';
            } else {
                this.mediaPreset = 'custom';
            }
            this.taskForm.media_types = types;
            this.refreshIcons();
        },

        isMediaTypeSelected(type) {
            return this.taskForm.media_types.includes(type) || (type !== 'all' && this.taskForm.media_types.includes('all'));
        },

        // Delay Presets
        applyDelayPreset(preset) {
            this.delayPreset = preset;
            if (preset === 'slow') {
                this.taskForm.delay_seconds = 20.0;
                this.taskForm.skip_delay_seconds = 1.0;
            } else if (preset === 'normal') {
                this.taskForm.delay_seconds = 10.0;
                this.taskForm.skip_delay_seconds = 0.5;
            } else if (preset === 'fast') {
                this.taskForm.delay_seconds = 3.0;
                this.taskForm.skip_delay_seconds = 0.3;
            }
            this.refreshIcons();
        },

        addCustomReplacementToTask() {
            if (!this.newRuleInTask.pattern) return;
            this.taskForm.custom_replacements.push({ ...this.newRuleInTask });
            this.newRuleInTask = { pattern: '', replacement: '', is_regex: false, enabled: true };
            this.refreshIcons();
        },

        removeCustomReplacementFromTask(index) {
            this.taskForm.custom_replacements.splice(index, 1);
            this.refreshIcons();
        },

        // Natural Language Summary for Cloner Step 4
        getTaskSummaryText() {
            const origin = this.getOriginDisplayName();
            const dest = this.getDestDisplayName();
            const mode = this.taskForm.mode === 'historical' ? 'todas as mensagens anteriores' : 'novas postagens em tempo real';
            let mediaText = 'todas as mídias e textos';
            if (this.mediaPreset === 'media_only') mediaText = 'apenas fotos, vídeos e documentos';
            else if (this.mediaPreset === 'text_only') mediaText = 'apenas mensagens de texto';
            else if (!this.taskForm.media_types.includes('all')) mediaText = `${this.taskForm.media_types.length} tipos de mídia selecionados`;

            const cleanArr = [];
            if (this.taskForm.clean_forward) cleanArr.push('reenvio limpo');
            if (this.taskForm.remove_captions) cleanArr.push('remoção de legendas/textos');
            if (this.taskForm.remove_links) cleanArr.push('remoção de links');
            if (this.taskForm.remove_mentions) cleanArr.push('remoção de menções');
            const cleanText = cleanArr.length > 0 ? ` com ${cleanArr.join(', ')}` : '';

            return `Copiar ${mode} (${mediaText}) de "${origin}" para "${dest}"${cleanText}. Intervalo de ${this.taskForm.delay_seconds}s entre mensagens.`;
        },

        // Tasks API Operations
        async fetchTasks(showLoading = false) {
            try {
                const res = await apiFetch('/api/tasks');
                if (res.ok) {
                    this.tasks = await res.json();
                }
            } catch (e) {
                console.error('Fetch tasks error', e);
            } finally {
                this.refreshIcons();
            }
        },

        async submitTaskForm() {
            if (!this.taskForm.origin_chat || !this.taskForm.dest_chat) {
                this.showToast('Informe o canal de Origem e Destino.', 'warning');
                return;
            }

            try {
                const payload = {
                    name: this.taskForm.name.trim() || 'Clonagem de Canal',
                    mode: this.taskForm.mode,
                    origin_chat: this.taskForm.origin_chat.trim(),
                    dest_chat: this.taskForm.dest_chat.trim(),
                    start_message_id: parseInt(this.taskForm.start_message_id) || 1,
                    end_message_id: this.taskForm.end_message_id ? parseInt(this.taskForm.end_message_id) : null,
                    media_types: this.taskForm.media_types,
                    clean_forward: this.taskForm.clean_forward,
                    delay_seconds: parseFloat(this.taskForm.delay_seconds) || 10.0,
                    skip_delay_seconds: parseFloat(this.taskForm.skip_delay_seconds) || 0.5,
                    remove_captions: Boolean(this.taskForm.remove_captions),
                    remove_links: this.taskForm.remove_links,
                    remove_mentions: this.taskForm.remove_mentions,
                    header_text: this.taskForm.header_text,
                    footer_text: this.taskForm.footer_text,
                    custom_replacements: this.taskForm.custom_replacements
                };

                let res, createdOrUpdated;
                if (this.isEditingTask && this.editingTaskId) {
                    res = await apiFetch(`/api/tasks/${this.editingTaskId}`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    if (res.ok) {
                        createdOrUpdated = await res.json();
                        this.showToast(`Tarefa "${createdOrUpdated.name}" atualizada!`, 'success');
                    }
                } else {
                    res = await apiFetch('/api/tasks', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    if (res.ok) {
                        createdOrUpdated = await res.json();
                        this.showToast(`Tarefa "${createdOrUpdated.name}" criada com sucesso!`, 'success');
                        
                        if (this.autoStartOnCreate && createdOrUpdated.id) {
                            await this.startTask(createdOrUpdated.id);
                        }
                    }
                }

                if (res.ok) {
                    await this.fetchTasks();
                    this.activeTab = 'tasks';
                } else {
                    const err = await res.json();
                    this.showToast(err.detail || 'Erro ao salvar tarefa.', 'error');
                }
            } catch (e) {
                this.showToast('Erro de conexão ao salvar tarefa.', 'error');
            } finally {
                this.refreshIcons();
            }
        },

        editTask(task) {
            this.isEditingTask = true;
            this.editingTaskId = task.id;
            this.wizardStep = 1;
            this.taskForm = {
                name: task.name,
                mode: task.mode,
                origin_chat: task.origin_chat,
                dest_chat: task.dest_chat,
                start_message_id: task.start_message_id || 1,
                end_message_id: task.end_message_id || null,
                media_types: task.media_types && task.media_types.length ? task.media_types : ['all'],
                clean_forward: task.clean_forward,
                delay_seconds: task.delay_seconds || 10.0,
                skip_delay_seconds: task.skip_delay_seconds || 0.5,
                remove_captions: Boolean(task.remove_captions),
                remove_links: task.remove_links,
                remove_mentions: task.remove_mentions,
                header_text: task.header_text || '',
                footer_text: task.footer_text || '',
                custom_replacements: task.custom_replacements || []
            };

            // Set presets
            if (this.taskForm.delay_seconds === 20.0) this.delayPreset = 'slow';
            else if (this.taskForm.delay_seconds === 10.0) this.delayPreset = 'normal';
            else if (this.taskForm.delay_seconds === 3.0) this.delayPreset = 'fast';
            else this.delayPreset = 'custom';

            if (this.taskForm.media_types.includes('all')) this.mediaPreset = 'all';
            else this.mediaPreset = 'custom';

            this.taskOriginCheck = { loading: false, title: task.origin_title, error: null };
            this.taskDestCheck = { loading: false, title: task.dest_title, error: null };

            this.activeTab = 'new-task';
            this.mobileDrawerOpen = false;
            this.refreshIcons();
        },

        duplicateTask(task) {
            this.isEditingTask = false;
            this.editingTaskId = null;
            this.wizardStep = 1;
            this.taskForm = {
                name: task.name + ' (Cópia)',
                mode: task.mode,
                origin_chat: task.origin_chat,
                dest_chat: task.dest_chat,
                start_message_id: 1,
                end_message_id: null,
                media_types: task.media_types && task.media_types.length ? [...task.media_types] : ['all'],
                clean_forward: task.clean_forward,
                delay_seconds: task.delay_seconds || 10.0,
                skip_delay_seconds: task.skip_delay_seconds || 0.5,
                remove_captions: Boolean(task.remove_captions),
                remove_links: task.remove_links,
                remove_mentions: task.remove_mentions,
                header_text: task.header_text || '',
                footer_text: task.footer_text || '',
                custom_replacements: task.custom_replacements ? JSON.parse(JSON.stringify(task.custom_replacements)) : []
            };
            this.taskOriginCheck = { loading: false, title: task.origin_title, error: null };
            this.taskDestCheck = { loading: false, title: task.dest_title, error: null };
            this.activeTab = 'new-task';
            this.mobileDrawerOpen = false;
            this.showToast(`Configurações de "${task.name}" duplicadas!`, 'info');
            this.refreshIcons();
        },

        async startTask(id) {
            try {
                const res = await apiFetch(`/api/tasks/${id}/start`, { method: 'POST' });
                const data = await res.json();
                if (res.ok) {
                    this.showToast('Tarefa iniciada!', 'success');
                    await this.fetchTasks();
                } else {
                    const msg = data.detail || 'Erro ao iniciar tarefa.';
                    this.showToast(msg, 'error');
                    if (msg.includes('Telegram') || msg.includes('sessão') || msg.includes('conecte')) {
                        this.openAuthModal();
                    }
                }
            } catch (e) {
                this.showToast('Erro ao comunicar com o servidor.', 'error');
            } finally {
                this.refreshIcons();
            }
        },

        async pauseTask(id) {
            try {
                const res = await apiFetch(`/api/tasks/${id}/pause`, { method: 'POST' });
                const data = await res.json();
                if (res.ok && data.success) {
                    this.showToast('Pausa solicitada. O motor aguardará a conclusão da mensagem atual.', 'info');
                    await this.fetchTasks();
                } else {
                    this.showToast(data.detail || 'Não foi possível pausar a tarefa.', 'error');
                }
            } catch (e) {
                this.showToast('Erro de conexão ao pausar.', 'error');
            } finally {
                this.refreshIcons();
            }
        },

        async resumeTask(id) {
            try {
                const res = await apiFetch(`/api/tasks/${id}/resume`, { method: 'POST' });
                const data = await res.json();
                if (res.ok && data.success) {
                    this.showToast('Tarefa retomada!', 'success');
                    await this.fetchTasks();
                } else {
                    const msg = data.detail || 'Não foi possível retomar a tarefa.';
                    this.showToast(msg, 'error');
                    if (msg.includes('Telegram') || msg.includes('sessão') || msg.includes('conecte')) {
                        this.openAuthModal();
                    }
                }
            } catch (e) {
                this.showToast('Erro de conexão ao retomar.', 'error');
            } finally {
                this.refreshIcons();
            }
        },

        async syncNewMessages(id) {
            this.syncingTaskId = id;
            try {
                this.showToast('Verificando novas mensagens no canal de origem...', 'info');
                const res = await apiFetch(`/api/tasks/${id}/sync-new`, { method: 'POST' });
                const data = await res.json();
                if (res.ok && data.success) {
                    if (data.new_messages > 0) {
                        this.showToast(data.message || `Sincronização iniciada com ${data.new_messages} novas mensagens!`, 'success');
                    } else {
                        this.showToast(data.message || 'O canal já está atualizado.', 'info');
                    }
                    await this.fetchTasks();
                } else {
                    const msg = data.detail || data.error || 'Não foi possível sincronizar a tarefa.';
                    this.showToast(msg, 'error');
                    if (msg.includes('Telegram') || msg.includes('sessão') || msg.includes('conecte')) {
                        this.openAuthModal();
                    }
                }
            } catch (e) {
                this.showToast('Erro de conexão ao sincronizar novas mensagens.', 'error');
            } finally {
                this.syncingTaskId = null;
                this.refreshIcons();
            }
        },

        async cancelTask(id) {
            try {
                const res = await apiFetch(`/api/tasks/${id}/cancel`, { method: 'POST' });
                const data = await res.json();
                if (res.ok && data.success) {
                    this.showToast('Tarefa cancelada.', 'warning');
                    await this.fetchTasks();
                } else {
                    this.showToast(data.detail || 'Não foi possível cancelar a tarefa.', 'error');
                }
            } catch (e) {
                this.showToast('Erro de conexão ao cancelar.', 'error');
            } finally {
                this.refreshIcons();
            }
        },

        // Modal Delete Confirmation
        promptDeleteTask(task) {
            this.deleteModal = {
                open: true,
                taskId: task.id,
                taskName: task.name
            };
            this.refreshIcons();
        },

        async confirmDeleteTask() {
            const id = this.deleteModal.taskId;
            if (!id) return;
            try {
                const res = await apiFetch(`/api/tasks/${id}`, { method: 'DELETE' });
                if (res.ok) {
                    this.showToast('Tarefa removida com sucesso.', 'info');
                    this.deleteModal.open = false;
                    await this.fetchTasks();
                }
            } catch (e) {
                this.showToast('Erro ao excluir tarefa.', 'error');
            } finally {
                this.refreshIcons();
            }
        },

        async toggleTaskLogs(taskId) {
            this.expandedTaskLogs[taskId] = !this.expandedTaskLogs[taskId];
            if (this.expandedTaskLogs[taskId]) {
                try {
                    const res = await apiFetch(`/api/tasks/${taskId}/logs`);
                    if (res.ok) {
                        const historyLogs = await res.json();
                        for (const hLog of historyLogs) {
                            if (!this.logs.some(l => l.task_id === hLog.task_id && l.timestamp === hLog.timestamp && l.message === hLog.message)) {
                                this.logs.push(hLog);
                            }
                        }
                    }
                } catch (e) {}
            }
            this.refreshIcons();
        },

        getTaskLogs(taskId) {
            return this.logs.filter(l => l.task_id === taskId);
        },

        // Status Translation & Strict Badge Styles
        getStatusLabel(status) {
            const map = {
                pending: 'Pendente',
                running: 'Em Execução',
                paused: 'Pausada',
                completed: 'Concluída',
                failed: 'Falha',
                cancelled: 'Cancelada'
            };
            return map[status] || status;
        },

        getStatusBadgeClass(status) {
            if (status === 'running') return 'bg-rose-500/15 text-rose-600 dark:text-rose-400 border border-rose-500/30 pulse-active';
            if (status === 'completed') return 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300 border border-gray-300 dark:border-gray-700';
            if (status === 'paused') return 'bg-gray-100 text-gray-600 dark:bg-gray-800/80 dark:text-gray-400 border border-gray-200 dark:border-gray-700';
            if (status === 'pending') return 'bg-gray-50 text-gray-500 dark:bg-gray-900 dark:text-gray-400 border border-gray-200 dark:border-gray-800';
            return 'bg-red-950/40 text-red-400 border border-red-800/40';
        },

        // Task Rate & ETA Calculation
        getTaskETA(task) {
            if (task.mode !== 'historical' || task.status !== 'running') return null;
            const remaining = (task.total_messages || 0) - (task.processed_messages || 0);
            if (remaining <= 0) return null;
            const delay = task.delay_seconds || 10.0;
            const totalSeconds = remaining * delay;
            const minutes = Math.ceil(totalSeconds / 60);
            if (minutes < 1) return 'Menos de 1 min';
            if (minutes < 60) return `~${minutes} min restantes`;
            const hours = Math.floor(minutes / 60);
            const remMin = minutes % 60;
            return `~${hours}h ${remMin}m restantes`;
        },

        // Rules API Operations
        async fetchRules() {
            try {
                const res = await apiFetch('/api/rules');
                if (res.ok) {
                    this.rules = await res.json();
                }
            } catch (e) {
                console.error(e);
            } finally {
                this.refreshIcons();
            }
        },

        async createGlobalRule() {
            if (!this.newGlobalRule.pattern) {
                this.showToast('Informe o padrão a ser substituído.', 'warning');
                return;
            }

            try {
                const res = await apiFetch('/api/rules', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name: this.newGlobalRule.name.trim() || `Regra ${this.newGlobalRule.pattern}`,
                        rule_type: 'replace',
                        pattern: this.newGlobalRule.pattern,
                        replacement: this.newGlobalRule.replacement,
                        is_regex: this.newGlobalRule.is_regex,
                        enabled: this.newGlobalRule.enabled
                    })
                });
                if (res.ok) {
                    this.showToast('Regra adicionada com sucesso!', 'success');
                    this.newGlobalRule = { name: '', pattern: '', replacement: '', is_regex: false, enabled: true };
                    await this.fetchRules();
                }
            } catch (e) {
                this.showToast('Erro ao criar regra.', 'error');
            } finally {
                this.refreshIcons();
            }
        },

        async toggleRuleEnabled(rule) {
            const nextState = !rule.enabled;
            try {
                const res = await apiFetch(`/api/rules/${rule.id}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ enabled: nextState })
                });
                if (res.ok) {
                    rule.enabled = nextState;
                    this.showToast(`Regra ${nextState ? 'ativada' : 'desativada'}.`, 'info');
                }
            } catch (e) {
                this.showToast('Erro ao alterar status da regra.', 'error');
            } finally {
                this.refreshIcons();
            }
        },

        async deleteGlobalRule(id) {
            try {
                const res = await apiFetch(`/api/rules/${id}`, { method: 'DELETE' });
                if (res.ok) {
                    this.showToast('Regra removida.', 'info');
                    await this.fetchRules();
                }
            } catch (e) {
                console.error(e);
            } finally {
                this.refreshIcons();
            }
        },

        // Metrics calculations
        get totalCopiedCount() {
            return this.tasks.reduce((sum, t) => sum + (t.copied_count || 0), 0);
        },

        get totalSkippedCount() {
            return this.tasks.reduce((sum, t) => sum + (t.skipped_count || 0), 0);
        },

        get totalErrorCount() {
            return this.tasks.reduce((sum, t) => sum + (t.error_count || 0), 0);
        },

        get activeTasksCount() {
            return this.tasks.filter(t => t.status === 'running').length;
        },

        get filteredTasks() {
            let result = this.tasks;
            if (this.taskFilter !== 'all') {
                result = result.filter(t => t.status === this.taskFilter);
            }
            if (this.taskSearch.trim()) {
                const q = this.taskSearch.toLowerCase();
                result = result.filter(t => 
                    (t.name && t.name.toLowerCase().includes(q)) ||
                    (t.origin_title && t.origin_title.toLowerCase().includes(q)) ||
                    (t.dest_title && t.dest_title.toLowerCase().includes(q)) ||
                    (t.origin_chat && t.origin_chat.toLowerCase().includes(q)) ||
                    (t.dest_chat && t.dest_chat.toLowerCase().includes(q))
                );
            }
            return result;
        },

        get filteredLogs() {
            let result = this.logs;
            if (this.logFilter !== 'all') {
                result = result.filter(l => l.level === this.logFilter);
            }
            if (this.logSearch.trim()) {
                const q = this.logSearch.toLowerCase();
                result = result.filter(l => l.message.toLowerCase().includes(q));
            }
            return result;
        },

        clearLogs() {
            this.logs = [];
        }
    }));
});
