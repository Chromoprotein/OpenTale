/**
 * OpenTale - Common JS functions
 */

// Check if document is ready
document.addEventListener('DOMContentLoaded', function() {
    // Enable Bootstrap tooltips
    var tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    var tooltipList = tooltipTriggerList.map(function(tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });

    // Auto-resize textareas
    document.querySelectorAll('textarea.auto-resize').forEach(function(textarea) {
        textarea.addEventListener('input', autoResizeTextarea);
        // Initial resize
        autoResizeTextarea.call(textarea);
    });

    // Handle active navigation links
    highlightActiveNav();

    // Handle chapter pagination
    handleChapterPagination();
});

/**
 * Auto-resize a textarea based on its content
 */
function autoResizeTextarea() {
    this.style.height = 'auto';
    this.style.height = (this.scrollHeight) + 'px';
}

/**
 * Highlight the active navigation link based on the current page
 */
function highlightActiveNav() {
    const currentPath = window.location.pathname;
    document.querySelectorAll('.nav-link').forEach(function(link) {
        if (link.getAttribute('href') === currentPath) {
            link.classList.add('active');
        }
    });
}

/**
 * Show a toast notification
 * @param {string} message - The message to display
 * @param {string} type - The type of notification (success, error, warning, info)
 */
function showNotification(message, type = 'info') {
    // Create toast element if it doesn't exist
    let toastContainer = document.querySelector('.toast-container');
    
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.className = 'toast-container position-fixed bottom-0 end-0 p-3';
        document.body.appendChild(toastContainer);
    }
    
    // Create the toast
    const toastId = 'toast-' + Date.now();
    const toast = document.createElement('div');
    toast.className = `toast align-items-center text-white bg-${type} border-0`;
    toast.id = toastId;
    toast.setAttribute('role', 'alert');
    toast.setAttribute('aria-live', 'assertive');
    toast.setAttribute('aria-atomic', 'true');
    
    toast.innerHTML = `
        <div class="d-flex">
            <div class="toast-body">
                ${message}
            </div>
            <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
        </div>
    `;
    
    toastContainer.appendChild(toast);
    
    // Show the toast
    const bsToast = new bootstrap.Toast(toast, {
        animation: true,
        autohide: true,
        delay: 3000
    });
    
    bsToast.show();
    
    // Remove the toast after it's hidden
    toast.addEventListener('hidden.bs.toast', function() {
        toast.remove();
    });
}

/**
 * Copy text to clipboard
 * @param {string} text - The text to copy
 * @returns {boolean} - Whether the copy was successful
 */
async function copyToClipboard(text) {
    if (!navigator.clipboard) {
        showNotification('Clipboard API not available', 'error');
        return false;
    }
    try {
        await navigator.clipboard.writeText(text);
        showNotification('Copied to clipboard!', 'success');
        return true;
    } catch (err) {
        console.error('Failed to copy text: ', err);
        showNotification('Failed to copy to clipboard', 'error');
        return false;
    }
}

/**
 * Save form data to local storage
 * @param {string} formId - The ID of the form to save
 * @param {string} storageKey - The key to use in localStorage
 */
function saveFormToLocalStorage(formId, storageKey) {
    const form = document.getElementById(formId);
    if (!form) return;
    
    const formData = {};
    const formElements = form.elements;
    
    for (let i = 0; i < formElements.length; i++) {
        const element = formElements[i];
        if (element.name && element.type !== 'submit' && element.type !== 'button') {
            formData[element.name] = element.value;
        }
    }
    
    localStorage.setItem(storageKey, JSON.stringify(formData));
}

/**
 * Load form data from local storage
 * @param {string} formId - The ID of the form to populate
 * @param {string} storageKey - The key to use in localStorage
 */
function loadFormFromLocalStorage(formId, storageKey) {
    const savedData = localStorage.getItem(storageKey);
    if (!savedData) return;
    
    const form = document.getElementById(formId);
    if (!form) return;
    
    const formData = JSON.parse(savedData);
    const formElements = form.elements;
    
    for (let i = 0; i < formElements.length; i++) {
        const element = formElements[i];
        if (element.name && formData[element.name] !== undefined) {
            element.value = formData[element.name];
        }
    }
} 


/**
 * Scroll the page to show the latest chat messages. Only called when the user
 * sends a message; streaming responses never move the scroll position.
 */
function scrollChatToBottom() {
    window.scrollTo(0, document.body.scrollHeight);
}

/**
 * True when a Quill editor is currently visible on the page. The composer is
 * unpinned while editing content exists so it doesn't float over it.
 */
function hasVisibleQuillEditor() {
    return Array.from(document.querySelectorAll('.quill-editor')).some(function(el) {
        return el.getClientRects().length > 0;
    });
}

/**
 * Keep the chat composer pinned to the bottom of the viewport on chat pages.
 * The composer's column is stretched down to the footer so position:sticky
 * has room to hold the composer at the bottom of the screen through the last
 * part of the scroll, with no empty gap left above the footer.
 */
function positionChatComposer() {
    const composer = document.querySelector('.chat-composer');
    if (!composer) {
        return;
    }
    const footer = document.querySelector('footer');
    const host = composer.closest('#chatContainer') || composer.closest('.chat-col');
    if (!host || !footer) {
        return;
    }

    // When a Quill editor is visible, let the composer scroll in normal flow
    // so it grows downward on resize instead of floating over the editor.
    const unpinned = hasVisibleQuillEditor();
    composer.classList.toggle('composer-in-flow', unpinned);
    if (unpinned) {
        if (host.style.minHeight !== '') {
            host.style.minHeight = '';
        }
        return;
    }

    // On full-width chat pages a visible sibling (e.g. a result container) can
    // follow the composer; when one is shown, don't stretch so no gap appears
    // above that content.
    if (host.id === 'chatContainer') {
        let node = host;
        while ((node = node.nextElementSibling) && node !== footer) {
            if (node.offsetParent !== null || node.getBoundingClientRect().height > 0) {
                if (host.style.minHeight !== '') {
                    host.style.minHeight = '';
                }
                return;
            }
        }
    }

    // Stretch the host so the composer lands at the bottom of the viewport.
    // The target is computed from the viewport and the host's document-top,
    // which are unaffected by stretching, so the value stabilizes after one
    // application (no feedback loop / runaway page height).
    const hostTop = host.getBoundingClientRect().top + window.scrollY;
    const target = Math.max(window.innerHeight - hostTop, 0);
    const desired = target + 'px';
    if (host.style.minHeight !== desired) {
        host.style.minHeight = desired;
    }
}

document.addEventListener('DOMContentLoaded', positionChatComposer);
window.addEventListener('load', positionChatComposer);
window.addEventListener('resize', positionChatComposer);
// Result containers are shown/hidden after chat finalization; re-position then.
new MutationObserver(positionChatComposer).observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['style', 'class']
});

/**
 * Save chat history to local storage
 * @param {string} key - The storage key
 * @param {Array} history - The chat history array ({role, content} objects)
 */
function saveChatHistory(key, history) {
    try {
        localStorage.setItem(key, JSON.stringify(history));
    } catch (e) {
        console.error('Failed to save chat history:', e);
        if (e.name === 'QuotaExceededError') {
            showNotification('Chat history is too large to save. Messages still work, but they may be lost on reload.', 'warning');
        }
    }
}

/**
 * Load chat history from local storage
 * @param {string} key - The storage key
 * @returns {Array} - The saved chat history, or [] if none
 */
function loadChatHistory(key) {
    try {
        const saved = localStorage.getItem(key);
        if (!saved) return [];
        const history = JSON.parse(saved);
        return Array.isArray(history) ? history : [];
    } catch (e) {
        console.error('Failed to load chat history:', e);
        return [];
    }
}

/**
 * Clear chat history from local storage
 * @param {string} key - The storage key
 */
function clearChatHistory(key) {
    try {
        localStorage.removeItem(key);
    } catch (e) {
        console.error('Failed to clear chat history:', e);
    }
}

/**
 * Enable or disable chat action buttons based on the current chat state.
 * @param {Array} chatHistory - The current chat history array
 * @param {Object} buttons - Selectors: { finalizeBtn, acceptBtn, sendBtn, messageInput, clearBtn }
 */
function updateChatButtonStates(chatHistory, buttons) {
    const hasChat = (chatHistory || []).length > 0;

    const finalizeBtn = $(buttons.finalizeBtn);
    if (finalizeBtn.length) {
        finalizeBtn.prop('disabled', !hasChat);
    }

    const acceptBtn = $(buttons.acceptBtn);
    if (acceptBtn.length) {
        acceptBtn.prop('disabled', !hasChat);
    }

    const clearBtn = $(buttons.clearBtn);
    if (clearBtn.length) {
        clearBtn.prop('disabled', !hasChat);
    }

    const sendBtn = $(buttons.sendBtn);
    if (sendBtn.length && buttons.messageInput) {
        const hasText = String($(buttons.messageInput).val() || '').trim().length > 0;
        sendBtn.prop('disabled', !hasText);
    }
}

/**
 * Enable editing and resubmitting past user chat messages.
 *
 * Clicking the pencil button on a user bubble prefills the composer with that
 * message and flags it as being edited. On resubmit the conversation is
 * truncated at the edited message (everything after it is discarded), the chat
 * area is repainted from the truncated history via renderChat, and the edited
 * message is passed to the page's own reply path via resubmit.
 *
 * @param {Object} opts
 *   container  - selector for the chat messages area (e.g. '#chatMessages')
 *   composer   - selector for the chat composer; the editing hint is prepended here
 *   input      - selector for the composer textarea
 *   getHistory - () => current chat history array
 *   setHistory - (Array) => assign the new chat history
 *   renderChat - () => repaint the chat area from the current history
 *   resubmit   - (message) => send the edited message through the page's reply path
 * @returns {Object} { active, cancel, submit, setBusy }
 */
function setupChatEditing(opts) {
    const state = {
        active: false,
        userIndex: -1,
        busy: false
    };

    const hint = $('<div>').addClass('edit-chat-hint d-none').append(
        $('<span>').html('<i class="bi bi-pencil me-1"></i>Editing this message.'),
        $('<button>').attr('type', 'button')
            .addClass('btn btn-link btn-sm p-0 edit-chat-cancel')
            .text('Cancel')
    );
    $(opts.composer).prepend(hint);

    const historyUserIndexes = function() {
        const indexes = [];
        const history = opts.getHistory();
        for (let i = 0; i < history.length; i++) {
            if (history[i] && history[i].role === 'user') {
                indexes.push(i);
            }
        }
        return indexes;
    };

    const userBubbles = function() {
        return $(opts.container).find('.user-msg');
    };

    const start = function(userIndex, message) {
        state.active = true;
        state.userIndex = userIndex;
        $(opts.input).val(message).trigger('input').focus();
        userBubbles().removeClass('editing-msg');
        userBubbles().eq(userIndex).addClass('editing-msg');
        hint.removeClass('d-none');
    };

    const cancel = function() {
        state.active = false;
        state.userIndex = -1;
        $(opts.input).val('').trigger('input');
        userBubbles().removeClass('editing-msg');
        hint.addClass('d-none');
    };

    const submit = function(message) {
        const userIndexes = historyUserIndexes();
        if (state.userIndex < 0 || state.userIndex >= userIndexes.length) {
            cancel();
            return;
        }
        const truncated = opts.getHistory()
            .slice(0, userIndexes[state.userIndex])
            .concat([{ role: 'user', content: message }]);
        state.active = false;
        state.userIndex = -1;
        $(opts.input).val('').trigger('input');
        hint.addClass('d-none');
        opts.setHistory(truncated);
        if (opts.renderChat) {
            opts.renderChat();
        }
        if (opts.resubmit) {
            opts.resubmit(message);
        }
    };

    $(opts.container).on('click', '.msg-edit-btn', function(e) {
        e.preventDefault();
        e.stopPropagation();
        if (state.busy || state.active) {
            return;
        }
        const userIndexes = historyUserIndexes();
        const bubbleIndex = userBubbles().index($(this).closest('.user-msg'));
        if (bubbleIndex < 0 || bubbleIndex >= userIndexes.length) {
            return;
        }
        start(bubbleIndex, opts.getHistory()[userIndexes[bubbleIndex]].content);
    });

    hint.on('click', '.edit-chat-cancel', cancel);

    $(opts.input).on('keydown', function(e) {
        if (e.key === 'Escape') {
            e.preventDefault();
            cancel();
        }
    });

    return {
        active: function() { return state.active; },
        cancel: cancel,
        submit: submit,
        setBusy: function(busy) { state.busy = busy; }
    };
}

/**
 * Show the shared confirmation modal and run the callback if confirmed.
 * @param {Object} config - { title, message, confirmText }
 * @param {Function} onConfirm - Runs after the user confirms.
 */
function confirmAction(config, onConfirm) {
    const modalEl = document.getElementById('confirmModal');
    const confirmBtn = document.getElementById('confirmActionBtn');
    if (!modalEl || !confirmBtn || typeof bootstrap === 'undefined') {
        onConfirm();
        return;
    }
    document.getElementById('confirmModalLabel').textContent = config.title || 'Confirm';
    document.getElementById('confirmModalBody').textContent = config.message || '';
    confirmBtn.textContent = config.confirmText || 'Confirm';
    const bsModal = bootstrap.Modal.getOrCreateInstance(modalEl);
    confirmBtn.onclick = function() {
        bsModal.hide();
        onConfirm();
    };
    bsModal.show();
}

/**
 * Confirm clearing the chat history.
 * @param {Function} onConfirm - Runs after the user confirms.
 */
function confirmClearChat(onConfirm) {
    confirmAction({
        title: 'Clear Chat',
        message: 'Clear the chat history? This cannot be undone.',
        confirmText: 'Clear Chat'
    }, onConfirm);
}

/**
 * Handle chapter navigation pagination
 */
function handleChapterPagination() {
    const chaptersPerPageSelect = document.getElementById('chaptersPerPage');
    const prevPageBtn = document.getElementById('prevPageBtn');
    const nextPageBtn = document.getElementById('nextPageBtn');
    const firstChapterBtn = document.getElementById('firstChapterBtn');
    const lastChapterBtn = document.getElementById('lastChapterBtn');
    const chapterNavContainer = document.querySelector('.list-group[data-total-chapters]');

    if (!chaptersPerPageSelect && !prevPageBtn && !nextPageBtn && !firstChapterBtn && !lastChapterBtn) {
        return; // Exit if no pagination controls are on this page
    }

    const url = new URL(window.location.href);
    const currentPage = parseInt(url.searchParams.get('page') || '1', 10);
    const perPage = parseInt(url.searchParams.get('per_page') || '10', 10);

    if (chaptersPerPageSelect) {
        chaptersPerPageSelect.value = perPage;
        chaptersPerPageSelect.addEventListener('change', (e) => {
            const newPerPage = e.target.value;
            url.searchParams.set('per_page', newPerPage);
            url.searchParams.set('page', '1'); // Reset to first page
            window.location.href = url.toString();
        });
    }

    if (prevPageBtn) {
        prevPageBtn.addEventListener('click', () => {
            if (currentPage > 1) {
                url.searchParams.set('page', currentPage - 1);
                window.location.href = url.toString();
            }
        });
    }

    if (nextPageBtn) {
        nextPageBtn.addEventListener('click', () => {
            url.searchParams.set('page', currentPage + 1);
            window.location.href = url.toString();
        });
    }

    const updateChapterView = (newChapterNumber) => {
        const currentPath = window.location.pathname;
        const pathParts = currentPath.split('/').filter(p => p); // filter out empty strings
        if (pathParts.length >= 2) {
            const view = pathParts[0];
            url.pathname = `/${view}/${newChapterNumber}`;
            window.location.href = url.toString();
        }
    };

    if (firstChapterBtn) {
        firstChapterBtn.addEventListener('click', () => {
            url.searchParams.set('page', '1');
            updateChapterView(1);
        });
    }

    if (lastChapterBtn && chapterNavContainer) {
        lastChapterBtn.addEventListener('click', () => {
            const totalChapters = parseInt(chapterNavContainer.dataset.totalChapters, 10);
            if (totalChapters) {
                const totalPages = Math.ceil(totalChapters / perPage);
                url.searchParams.set('page', totalPages);
                updateChapterView(totalChapters);
            }
        });
    }
}

/**
 * Shows a Bootstrap modal with a title and pre-formatted content.
 * @param {string} title The title of the modal.
 * @param {string} content The pre-formatted content to display.
 */
function showModalWithContent(title, content) {
    // Remove any existing modals
    const existingModal = document.getElementById('dynamicModal');
    if (existingModal) {
        existingModal.remove();
    }

    // Create modal elements
    const modal = document.createElement('div');
    modal.className = 'modal fade';
    modal.id = 'dynamicModal';
    modal.tabIndex = -1;
    modal.innerHTML = `
        <div class="modal-dialog modal-xl modal-fullscreen-lg-down">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">${title}</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body modal-body-scrollable">
                    <div class="d-flex justify-content-end">
                        <button type="button" class="btn btn-link btn-sm copy-icon" id="copyModalContent" data-bs-toggle="tooltip" data-bs-placement="bottom" title="Copy to Clipboard">
                            <i class="bi bi-clipboard"></i>
                        </button>
                    </div>
                    <pre><code id="modalContent">${content.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</code></pre>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
                </div>
            </div>
        </div>
    `;

    document.body.appendChild(modal);

    const bsModal = new bootstrap.Modal(modal);

    // Handle copy button clicks
    document.getElementById('copyModalContent').addEventListener('click', function() {
        copyToClipboard(content);
    });

    // Initialize tooltips within the new modal
    const tooltipTriggerList = [].slice.call(modal.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });

    // Clean up after modal is hidden
    modal.addEventListener('hidden.bs.modal', function() {
        modal.remove();
    });

    bsModal.show();
}

/**
 * Shows a Bootstrap modal with two tabs, each containing pre-formatted content and a copy-to-clipboard button.
 * @param {string} title The title of the modal.
 * @param {string} tab1Title The title of the first tab.
 * @param {string} tab1Content The pre-formatted content for the first tab.
 * @param {string} tab2Title The title of the second tab.
 * @param {string} tab2Content The pre-formatted content for the second tab.
 */
function showDualContentModal(title, tab1Title, tab1Content, tab2Title, tab2Content) {
    // Remove any existing modals
    const existingModal = document.getElementById('dualContentModal');
    if (existingModal) {
        existingModal.remove();
    }

    // Create modal elements
    const modal = document.createElement('div');
    modal.className = 'modal fade';
    modal.id = 'dualContentModal';
    modal.tabIndex = -1;
    modal.style.zIndex = 1050; /* Ensure modal is on top */
    modal.innerHTML = `
        <div class="modal-dialog modal-xl modal-fullscreen-lg-down">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">${title}</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body modal-body-scrollable">
                    <ul class="nav nav-tabs" id="myTab" role="tablist">
                        <li class="nav-item" role="presentation">
                            <button class="nav-link active" id="tab1-tab" data-bs-toggle="tab" data-bs-target="#tab1-pane" type="button" role="tab" aria-controls="tab1-pane" aria-selected="true">${tab1Title}</button>
                        </li>
                        <li class="nav-item" role="presentation">
                            <button class="nav-link" id="tab2-tab" data-bs-toggle="tab" data-bs-target="#tab2-pane" type="button" role="tab" aria-controls="tab2-pane" aria-selected="false">${tab2Title}</button>
                        </li>
                    </ul>
                    <div class="tab-content" id="myTabContent">
                        <div class="tab-pane fade show active" id="tab1-pane" role="tabpanel" aria-labelledby="tab1-tab" tabindex="0">
                            <div class="d-flex justify-content-end mt-2">
                                <button type="button" class="btn btn-link btn-sm copy-icon" id="copyTab1Content" data-bs-toggle="tooltip" data-bs-placement="bottom" title="Copy to Clipboard">
                                    <i class="bi bi-clipboard"></i>
                                </button>
                            </div>
                            <pre><code id="tab1Content">${tab1Content.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</code></pre>
                        </div>
                        <div class="tab-pane fade" id="tab2-pane" role="tabpanel" aria-labelledby="tab2-tab" tabindex="0">
                            <div class="d-flex justify-content-end mt-2">
                                <button type="button" class="btn btn-link btn-sm copy-icon" id="copyTab2Content" data-bs-toggle="tooltip" data-bs-placement="bottom" title="Copy to Clipboard">
                                    <i class="bi bi-clipboard"></i>
                                </button>
                            </div>
                            <pre><code id="tab2Content">${tab2Content.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</code></pre>
                        </div>
                    </div>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
                </div>
            </div>
        </div>
    `;

    document.body.appendChild(modal);

    const bsModal = new bootstrap.Modal(modal);

    // Handle copy button clicks
    document.getElementById('copyTab1Content').addEventListener('click', function() {
        copyToClipboard(tab1Content);
    });
    document.getElementById('copyTab2Content').addEventListener('click', function() {
        copyToClipboard(tab2Content);
    });

    // Clean up after modal is hidden
    modal.addEventListener('hidden.bs.modal', function() {
        modal.remove();
    });

    bsModal.show();
}

/**
 * Light/dark theme toggle. The current data-bs-theme is applied to <html>
 * early in base.html; this keeps it in sync with user interaction.
 */
(function themeToggle() {
    const button = document.getElementById('themeToggle');
    if (!button) {
        return;
    }

    const updateIcon = () => {
        const isDark = document.documentElement.getAttribute('data-bs-theme') === 'dark';
        button.innerHTML = isDark
            ? '<i class="bi bi-sun-fill"></i>'
            : '<i class="bi bi-moon-stars-fill"></i>';
    };

    button.addEventListener('click', () => {
        const isDark = document.documentElement.getAttribute('data-bs-theme') === 'dark';
        const next = isDark ? 'light' : 'dark';
        document.documentElement.setAttribute('data-bs-theme', next);
        localStorage.setItem('theme', next);
        updateIcon();
    });

    updateIcon();
})();
