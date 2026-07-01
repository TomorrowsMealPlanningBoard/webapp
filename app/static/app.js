document.addEventListener("DOMContentLoaded", () => {
    // 状態管理
    const state = {
        allergies: [],
        dislikes: [],
        token: localStorage.getItem("tomorrows_meal_token") || null
    };

    // DOM要素
    const authView = document.getElementById("auth-view");
    const appView = document.getElementById("app-view");
    
    // タブ要素
    const tabLogin = document.getElementById("tab-login");
    const tabRegister = document.getElementById("tab-register");
    const loginForm = document.getElementById("login-form");
    const registerForm = document.getElementById("register-form");

    // プロファイルフォーム要素
    const profileForm = document.getElementById("profile-form");
    const displayNameInput = document.getElementById("display-name");
    const allergyInput = document.getElementById("allergy-input");
    const addAllergyBtn = document.getElementById("add-allergy-btn");
    const allergyTagsContainer = document.getElementById("allergy-tags");
    const dislikeInput = document.getElementById("dislike-input");
    const addDislikeBtn = document.getElementById("add-dislike-btn");
    const dislikeTagsContainer = document.getElementById("dislike-tags");
    const saveProfileBtn = document.getElementById("save-profile-btn");
    const toastContainer = document.getElementById("toast-container");
    const logoutBtn = document.getElementById("logout-btn");
    const goalOtherWrap = document.getElementById("goal-other-input-wrap");
    const goalOtherText = document.getElementById("goal-other-text");

    // 「その他」選択時に自由記述欄を開閉
    document.querySelectorAll('input[name="goal"]').forEach((radio) => {
        radio.addEventListener("change", () => {
            if (radio.value === "other") {
                goalOtherWrap.classList.remove("hidden");
                goalOtherText.focus();
            } else {
                goalOtherWrap.classList.add("hidden");
            }
        });
    });

    // トースト通知を表示 (daisyUI alert + toast)
    function showToast(message, type = "success") {
        const alertDiv = document.createElement("div");
        const alertClass = type === "success" ? "alert-success" : "alert-error";
        alertDiv.className = `alert ${alertClass} shadow-lg py-3 px-4 flex items-center gap-2 transition-all duration-300 transform translate-y-4 opacity-0`;

        const icon = type === "success" ? "✅" : "⚠️";
        alertDiv.innerHTML = `
            <span class="text-base">${icon}</span>
            <span class="text-sm font-bold">${message}</span>
        `;

        toastContainer.appendChild(alertDiv);

        // アニメーション付き表示
        requestAnimationFrame(() => {
            alertDiv.classList.remove("translate-y-4", "opacity-0");
        });

        // 3秒後にフェードアウトして削除
        setTimeout(() => {
            alertDiv.classList.add("translate-y-4", "opacity-0");
            alertDiv.addEventListener("transitionend", () => {
                alertDiv.remove();
            });
        }, 3000);
    }

    // 認証ヘッダーヘルパー
    function getAuthHeaders() {
        return {
            "Content-Type": "application/json",
            "Authorization": `Bearer ${state.token}`
        };
    }

    // 認証ビューのトグル (タブ切り替え)
    tabLogin.addEventListener("click", () => {
        tabLogin.classList.add("tab-active");
        tabRegister.classList.remove("tab-active");
        loginForm.classList.remove("hidden");
        registerForm.classList.add("hidden");
    });

    tabRegister.addEventListener("click", () => {
        tabRegister.classList.add("tab-active");
        tabLogin.classList.remove("tab-active");
        registerForm.classList.remove("hidden");
        loginForm.classList.add("hidden");
    });

    // ログイン処理
    loginForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const email = document.getElementById("login-email").value.trim();
        const password = document.getElementById("login-password").value;
        const submitBtn = document.getElementById("login-submit-btn");

        if (!email || !password) {
            showToast("メールアドレスとパスワードを入力してください。", "error");
            return;
        }

        submitBtn.disabled = true;
        const spinner = document.createElement("span");
        spinner.className = "loading loading-spinner loading-sm mr-2";
        submitBtn.prepend(spinner);

        try {
            // FastAPI の OAuth2PasswordRequestForm は form-data 形式で受け取る
            // username フィールドにメールアドレスをセットする
            const formData = new URLSearchParams();
            formData.append("username", email);
            formData.append("password", password);

            const response = await fetch("/api/auth/login", {
                method: "POST",
                headers: { "Content-Type": "application/x-www-form-urlencoded" },
                body: formData.toString()
            });

            if (!response.ok) {
                const errData = await response.json();
                throw new Error(errData.detail || "ログインに失敗しました。");
            }

            const data = await response.json();
            showToast("ログインしました！", "success");
            loginSuccess(data.access_token);
        } catch (error) {
            showToast(error.message, "error");
        } finally {
            submitBtn.disabled = false;
            const sp = submitBtn.querySelector(".loading-spinner");
            if (sp) sp.remove();
        }
    });

    // 会員登録処理
    registerForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const displayName = document.getElementById("register-display-name").value.trim();
        const email = document.getElementById("register-email").value.trim();
        const password = document.getElementById("register-password").value;
        const submitBtn = document.getElementById("register-submit-btn");

        if (!email || !password) {
            showToast("メールアドレスとパスワードを入力してください。", "error");
            return;
        }
        if (password.length < 6) {
            showToast("パスワードは6文字以上で入力してください。", "error");
            return;
        }

        submitBtn.disabled = true;
        const spinner = document.createElement("span");
        spinner.className = "loading loading-spinner loading-sm mr-2";
        submitBtn.prepend(spinner);

        try {
            const response = await fetch("/api/auth/register", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email, password, display_name: displayName || null })
            });

            if (!response.ok) {
                const errData = await response.json();
                throw new Error(errData.detail || "登録に失敗しました。");
            }

            const data = await response.json();
            showToast("アカウントを作成しました！", "success");
            loginSuccess(data.access_token);
        } catch (error) {
            showToast(error.message, "error");
        } finally {
            submitBtn.disabled = false;
            const sp = submitBtn.querySelector(".loading-spinner");
            if (sp) sp.remove();
        }
    });

    // ログイン成功時の処理
    function loginSuccess(token) {
        state.token = token;
        localStorage.setItem("tomorrows_meal_token", token);
        showView();
        fetchProfile();
    }

    // ログアウト処理
    logoutBtn.addEventListener("click", () => {
        state.token = null;
        localStorage.removeItem("tomorrows_meal_token");
        showView();
        showToast("ログアウトしました。", "success");
        // フォームのリセット
        displayNameInput.value = "";
        state.allergies = [];
        state.dislikes = [];
        renderTags("allergy");
        renderTags("dislike");
        document.getElementById("goal-other").checked = true;
        goalOtherText.value = "";
        goalOtherWrap.classList.add("hidden");
        document.querySelectorAll('input[name="kitchen_tools"]').forEach(cb => cb.checked = false);
    });

    // ログイン状態に応じたビュー切り替え
    function showView() {
        if (state.token) {
            authView.classList.add("hidden");
            appView.classList.remove("hidden");
        } else {
            authView.classList.remove("hidden");
            appView.classList.add("hidden");
        }
    }

    // タグの描画 (daisyUIバッジ)
    function renderTags(type) {
        const container = type === "allergy" ? allergyTagsContainer : dislikeTagsContainer;
        const list = type === "allergy" ? state.allergies : state.dislikes;

        container.innerHTML = "";
        list.forEach((item, index) => {
            const tag = document.createElement("div");
            tag.className = "badge badge-neutral badge-outline gap-1.5 p-3.5 text-sm font-medium";
            tag.innerHTML = `
                <span>${item}</span>
                <button type="button" class="btn btn-ghost btn-xs p-0 min-h-0 h-4 w-4 text-base-content/50 hover:text-error hover:bg-transparent font-bold flex items-center justify-center" data-index="${index}">&times;</button>
            `;

            tag.querySelector("button").addEventListener("click", () => {
                removeTag(type, index);
            });

            container.appendChild(tag);
        });
    }

    // タグの追加
    function addTag(type) {
        const input = type === "allergy" ? allergyInput : dislikeInput;
        const value = input.value.trim();
        if (!value) return;

        const list = type === "allergy" ? state.allergies : state.dislikes;
        if (!list.includes(value)) {
            list.push(value);
            renderTags(type);
        }
        input.value = "";
        input.focus();
    }

    // タグの削除
    function removeTag(type, index) {
        const list = type === "allergy" ? state.allergies : state.dislikes;
        list.splice(index, 1);
        renderTags(type);
    }

    // イベントリスナー設定
    addAllergyBtn.addEventListener("click", () => addTag("allergy"));
    allergyInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            e.preventDefault();
            addTag("allergy");
        }
    });

    addDislikeBtn.addEventListener("click", () => addTag("dislike"));
    dislikeInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            e.preventDefault();
            addTag("dislike");
        }
    });

    // プロファイルの初期値取得
    async function fetchProfile() {
        if (!state.token) return;
        try {
            const response = await fetch("/api/profile", {
                headers: getAuthHeaders()
            });
            
            if (response.status === 401) {
                // 認証切れの場合はログアウト処理
                state.token = null;
                localStorage.removeItem("tomorrows_meal_token");
                showView();
                return;
            }
            
            if (!response.ok) throw new Error("プロファイルの読み込みに失敗しました");
            const data = await response.json();

            // 表示名
            displayNameInput.value = data.display_name || "";

            // preferences
            if (data.preferences) {
                state.allergies = data.preferences.allergies || [];
                state.dislikes = data.preferences.dislikes || [];
                renderTags("allergy");
                renderTags("dislike");

                // 目標の選択
                const goal = data.preferences.goal || "other";
                const knownGoals = ["diet", "bulk", "maintain", "other"];
                if (knownGoals.includes(goal)) {
                    const goalRadio = document.getElementById(`goal-${goal}`);
                    if (goalRadio) goalRadio.checked = true;
                    if (goal === "other") goalOtherWrap.classList.remove("hidden");
                } else {
                    // 自由記述が保存されている場合
                    document.getElementById("goal-other").checked = true;
                    goalOtherText.value = goal;
                    goalOtherWrap.classList.remove("hidden");
                }

                // 調理器具の選択状態を復元
                const kitchenTools = data.preferences.kitchen_tools || [];
                const toolCheckboxes = document.querySelectorAll('input[name="kitchen_tools"]');
                toolCheckboxes.forEach(checkbox => {
                    checkbox.checked = kitchenTools.includes(checkbox.value);
                });
            }
        } catch (error) {
            console.error(error);
            showToast("プロファイルの初期化に失敗しました", "error");
        }
    }

    // フォームのバリデーションと送信
    profileForm.addEventListener("submit", async (e) => {
        e.preventDefault();

        // バリデーション
        const displayName = displayNameInput.value.trim();
        const nameError = document.getElementById("name-error");
        if (!displayName) {
            displayNameInput.classList.add("input-error");
            nameError.classList.remove("hidden");
            displayNameInput.focus();
            return;
        } else {
            displayNameInput.classList.remove("input-error");
            nameError.classList.add("hidden");
        }

        // 選択された調理器具の取得
        const selectedTools = [];
        document.querySelectorAll('input[name="kitchen_tools"]:checked').forEach(checkbox => {
            selectedTools.push(checkbox.value);
        });

        // 送信データの構築
        const selectedGoal = document.querySelector('input[name="goal"]:checked').value;
        const goalValue = selectedGoal === "other"
            ? (goalOtherText.value.trim() || "other")
            : selectedGoal;
        const payload = {
            display_name: displayName,
            preferences: {
                allergies: state.allergies,
                dislikes: state.dislikes,
                goal: goalValue,
                kitchen_tools: selectedTools
            }
        };

        // ローディング開始 (daisyUI loading-spinnerを挿入)
        saveProfileBtn.disabled = true;
        saveProfileBtn.classList.add("btn-disabled");
        const spinner = document.createElement("span");
        spinner.className = "loading loading-spinner loading-sm";
        saveProfileBtn.prepend(spinner);

        try {
            const response = await fetch("/api/profile", {
                method: "PUT",
                headers: getAuthHeaders(),
                body: JSON.stringify(payload)
            });

            if (response.status === 401) {
                state.token = null;
                localStorage.removeItem("tomorrows_meal_token");
                showView();
                throw new Error("セッションの期限が切れました。再度ログインしてください。");
            }

            if (!response.ok) throw new Error("保存に失敗しました");

            showToast("設定を保存しました！", "success");
        } catch (error) {
            console.error(error);
            showToast(error.message || "保存中にエラーが発生しました", "error");
        } finally {
            // ローディング終了
            saveProfileBtn.disabled = false;
            saveProfileBtn.classList.remove("btn-disabled");
            const spinnerEl = saveProfileBtn.querySelector(".loading-spinner");
            if (spinnerEl) spinnerEl.remove();
        }
    });

    // 初期化実行
    showView();
    fetchProfile();
});
