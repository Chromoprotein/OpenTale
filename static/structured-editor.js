/**
 * OpenTale Structured Editor
 *
 * Lightweight helpers for the structured world/character forms. Fields are
 * plain <textarea>s (no rich text) so their values map cleanly to JSON. Each
 * field can still use the inline AI endpoints to revise the selected text or
 * continue writing from the cursor.
 *
 * The inline AI backend (/inline_llm_revise_stream and
 * /inline_llm_continue_stream) is editor-agnostic, so textareas work just as
 * well as Quill editors.
 */
(function () {
    'use strict';

    const API_REVISE_STREAM = '/inline_llm_revise_stream';
    const API_CONTINUE_STREAM = '/inline_llm_continue_stream';

    function getModal() {
        let modal = document.getElementById('structuredAiModal');
        if (modal) return modal;

        modal = document.createElement('div');
        modal.className = 'modal fade';
        modal.id = 'structuredAiModal';
        modal.tabIndex = -1;
        modal.innerHTML = `
            <div class="modal-dialog modal-xl modal-fullscreen-lg-down">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title">AI Writing</h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                    </div>
                    <div class="modal-body">
                        <div class="mb-3">
                            <label class="form-label fw-bold" id="structuredAiContextLabel">Context:</label>
                            <div class="border rounded p-3 bg-body-secondary" style="max-height: 10em; overflow-y: auto;">
                                <pre class="mb-0" style="white-space: pre-wrap; word-wrap: break-word;"><code id="structuredAiContext"></code></pre>
                            </div>
                        </div>
                        <div class="mb-3 d-none" id="structuredAiSuggestionSection">
                            <label class="form-label fw-bold">Suggestion:</label>
                            <div class="border rounded p-3 bg-body-secondary" id="structuredAiSuggestionBox"
                                 style="max-height: 18em; min-height: 6em; overflow-y: auto; white-space: pre-wrap; word-wrap: break-word;"></div>
                        </div>
                        <div class="mb-3">
                            <label for="structuredAiPrompt" class="form-label">Additional Instructions? (Optional)</label>
                            <textarea class="form-control" id="structuredAiPrompt" rows="2"
                                      placeholder="Enter any additional instructions..."></textarea>
                        </div>
                        <p class="text-muted small mb-0" id="structuredAiNote"></p>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
                        <button type="button" class="btn btn-primary" id="structuredAiStartBtn">Generate</button>
                        <button type="button" class="btn btn-warning d-none" id="structuredAiStopBtn"><i class="bi bi-stop-fill me-1"></i>Stop</button>
                        <button type="button" class="btn btn-success d-none" id="structuredAiAcceptBtn"><i class="bi bi-check-lg me-1"></i>Accept</button>
                        <button type="button" class="btn btn-outline-danger d-none" id="structuredAiRejectBtn"><i class="bi bi-x-lg me-1"></i>Reject</button>
                    </div>
                </div>
            </div>`;
        document.body.appendChild(modal);
        return modal;
    }

    function openForTextarea(textarea) {
        const value = textarea.value || '';
        const start = textarea.selectionStart || 0;
        const end = textarea.selectionEnd || 0;
        const isRevise = end > start;
        const passage = isRevise ? value.slice(start, end) : value.slice(0, start) || value;
        if (!passage.trim()) return;

        const modal = getModal();
        const contextLabel = modal.querySelector('#structuredAiContextLabel');
        const contextEl = modal.querySelector('#structuredAiContext');
        const suggestionSection = modal.querySelector('#structuredAiSuggestionSection');
        const suggestionBox = modal.querySelector('#structuredAiSuggestionBox');
        const promptInput = modal.querySelector('#structuredAiPrompt');
        const note = modal.querySelector('#structuredAiNote');
        const startBtn = modal.querySelector('#structuredAiStartBtn');
        const stopBtn = modal.querySelector('#structuredAiStopBtn');
        const acceptBtn = modal.querySelector('#structuredAiAcceptBtn');
        const rejectBtn = modal.querySelector('#structuredAiRejectBtn');

        modal.querySelector('.modal-title').textContent = isRevise ? 'Revise Text' : 'Continue Writing';
        contextLabel.textContent = isRevise ? 'Text to Revise' : 'Current Text';
        note.textContent = isRevise
            ? 'Accepting replaces the selected passage with the suggestion.'
            : 'Accepting inserts the suggestion at the cursor position.';
        startBtn.textContent = isRevise ? 'Revise' : 'Continue';
        contextEl.textContent = passage;
        suggestionBox.textContent = '';
        suggestionSection.classList.add('d-none');
        promptInput.value = '';
        promptInput.disabled = false;
        startBtn.classList.remove('d-none');
        startBtn.disabled = false;
        stopBtn.classList.add('d-none');
        acceptBtn.classList.add('d-none');
        rejectBtn.classList.add('d-none');

        const controller = new AbortController();
        modal.__aiController = controller;

        startBtn.onclick = function () {
            streamSuggestion({
                isRevise: isRevise,
                passage: passage,
                controller: controller,
                promptInput: promptInput,
                suggestionSection: suggestionSection,
                suggestionBox: suggestionBox,
                startBtn: startBtn,
                stopBtn: stopBtn,
                acceptBtn: acceptBtn,
                rejectBtn: rejectBtn
            });
        };
        stopBtn.onclick = function () {
            controller.abort();
        };
        acceptBtn.onclick = function () {
            const text = suggestionBox.textContent;
            if (!text) return;
            if (isRevise) {
                textarea.value = value.slice(0, start) + text + value.slice(end);
                textarea.setSelectionRange(start + text.length, start + text.length);
            } else {
                textarea.value = value.slice(0, start) + text + value.slice(start);
                textarea.setSelectionRange(start + text.length, start + text.length);
            }
            textarea.dispatchEvent(new Event('input', { bubbles: true }));
            textarea.focus();
            bootstrap.Modal.getOrCreateInstance(modal).hide();
        };

        modal.querySelector('.modal-title').textContent = isRevise ? 'Revise Text' : 'Continue Writing';
        contextLabel.textContent = isRevise ? 'Text to Revise' : 'Current Text';
        contextEl.textContent = passage;
        note.textContent = isRevise
            ? 'Accepting replaces the selected passage with the suggestion.'
            : 'Accepting inserts the suggestion at the cursor position.';
        suggestionSection.classList.add('d-none');
        suggestionBox.textContent = '';
        promptInput.value = '';
        startBtn.classList.remove('d-none');
        stopBtn.classList.add('d-none');
        acceptBtn.classList.add('d-none');
        rejectBtn.classList.add('d-none');

        bootstrap.Modal.getOrCreateInstance(modal).show();
    }

    async function streamSuggestion(opts) {
        const { controller, promptInput, suggestionSection, suggestionBox } = opts;
        const { startBtn, stopBtn, acceptBtn, rejectBtn } = opts;

        suggestionBox.textContent = '';
        suggestionSection.classList.remove('d-none');
        startBtn.classList.add('d-none');
        stopBtn.classList.remove('d-none');
        acceptBtn.classList.add('d-none');
        rejectBtn.classList.add('d-none');
        promptInput.disabled = true;

        try {
            const response = await fetch(opts.isRevise ? API_REVISE_STREAM : API_CONTINUE_STREAM, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    context: opts.passage,
                    user_prompt: promptInput.value,
                    chapter_outline: ''
                }),
                signal: controller.signal
            });
            if (!response.body) throw new Error('ReadableStream not available.');
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                const chunk = decoder.decode(value, { stream: true });
                for (const line of chunk.split('\n')) {
                    if (!line.startsWith('data: ')) continue;
                    const jsonString = line.substring(6);
                    if (!jsonString) continue;
                    try {
                        const data = JSON.parse(jsonString);
                        if (data.content === '[DONE]') return;
                        if (data.content) suggestionBox.textContent += data.content;
                    } catch (e) {
                        console.error('Error parsing stream data:', e);
                    }
                }
            }
        } catch (error) {
            if (error.name !== 'AbortError') console.error('Error fetching LLM stream:', error);
        } finally {
            promptInput.disabled = false;
            stopBtn.classList.add('d-none');
            if (suggestionBox.textContent.trim()) {
                acceptBtn.classList.remove('d-none');
                rejectBtn.classList.remove('d-none');
            } else {
                startBtn.classList.remove('d-none');
            }
        }
    }

    function init(root) {
        const scope = root || document;
        scope.querySelectorAll('.structured-field').forEach(function (textarea) {
            if (textarea.dataset.structuredReady) return;
            textarea.dataset.structuredReady = '1';

            const wrapper = document.createElement('div');
            wrapper.className = 'd-flex align-items-center gap-2 mb-1';
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'btn btn-outline-primary btn-sm';
            button.innerHTML = '<i class="bi bi-magic me-1"></i>AI Writing';
            button.title = 'Revise selected text or continue writing with AI';
            button.addEventListener('click', function () {
                openForTextarea(textarea);
            });
            wrapper.appendChild(button);
            textarea.parentNode.insertBefore(wrapper, textarea);
        });
    }

    window.StructuredEditor = { init: init };
    document.addEventListener('DOMContentLoaded', function () {
        init(document);
    });
})();
