document.addEventListener("DOMContentLoaded", () => {
    // 状態管理
    const state = {
        allergies: [],
        dislikes: []
    };

    // DOM要素
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

    // タグの描画 (daisyUIバッジ)
    function renderTags(type) {
        const container = type === "allergy" ? allergyTagsContainer : dislikeTagsContainer;
        const list = type === "allergy" ? state.allergies : state.dislikes;
        
        container.innerHTML = "";
        list.forEach((item, index) => {
            const tag = document.createElement("div");
            // daisyUI の丸みとアウトラインバッジを利用
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
        try {
            const response = await fetch("/api/profile");
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
                const goal = data.preferences.goal || "none";
                const goalRadio = document.getElementById(`goal-${goal}`);
                if (goalRadio) {
                    goalRadio.checked = true;
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
        const payload = {
            display_name: displayName,
            preferences: {
                allergies: state.allergies,
                dislikes: state.dislikes,
                goal: selectedGoal,
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
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify(payload)
            });

            if (!response.ok) throw new Error("保存に失敗しました");
            
            showToast("設定を保存しました！", "success");
        } catch (error) {
            console.error(error);
            showToast("保存中にエラーが発生しました", "error");
        } finally {
            // ローディング終了
            saveProfileBtn.disabled = false;
            saveProfileBtn.classList.remove("btn-disabled");
            const spinnerEl = saveProfileBtn.querySelector(".loading-spinner");
            if (spinnerEl) spinnerEl.remove();
        }
    });

    // 初期化実行
    fetchProfile();
});
