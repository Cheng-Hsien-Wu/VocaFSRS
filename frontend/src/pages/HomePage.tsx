import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { db } from '../db/dexie';
import { STUDY_SUMMARY_SESSION_STORAGE_KEY } from '../domain';
import { api, type LlmProvider, type LlmSettingsUpdate } from '../services/api';
import { useHomeStatus } from '../hooks/useHomeStatus';
import { MaterialSymbol } from '../components/MaterialSymbol';
import { buildMainAction, nextDueLabel } from '../services/home-actions';

interface HomePageProps {
  theme: 'light' | 'dark';
  onToggleTheme: () => void;
}

// Session size options
const PLACEMENT_OPTIONS = [
  { count: 100, label: '100 字' },
  { count: 250, label: '250 字' },
  { count: 500, label: '500 字' },
];

const STUDY_OPTIONS = [
  { count: 10, label: '10 題' },
  { count: 25, label: '25 題' },
  { count: 50, label: '50 題' },
  { count: 100, label: '100 題' },
];

const DEFAULT_OPENAI_COMPATIBLE_BASE_URL = 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions';
const LLM_PROVIDER_OPTIONS: { value: LlmProvider; label: string }[] = [
  { value: 'auto', label: 'Auto' },
  { value: 'gemini', label: 'Gemini native' },
  { value: 'openai_compatible', label: 'OpenAI-compatible' },
  { value: 'openrouter', label: 'OpenRouter' },
];

export default function HomePage({ theme, onToggleTheme }: HomePageProps) {
  const navigate = useNavigate();
  const [resetArmed, setResetArmed] = useState(false);
  const [isResetting, setIsResetting] = useState(false);
  const [resetStatus, setResetStatus] = useState<string>('');
  const [llmProvider, setLlmProvider] = useState<LlmProvider>('auto');
  const [llmModel, setLlmModel] = useState('');
  const [llmBaseUrl, setLlmBaseUrl] = useState('');
  const [llmApiKey, setLlmApiKey] = useState('');
  const [llmTimeout, setLlmTimeout] = useState(45);
  const [llmKeyStatus, setLlmKeyStatus] = useState('');
  const [llmStatus, setLlmStatus] = useState('');
  const [isSavingLlm, setIsSavingLlm] = useState(false);
  const [isTestingLlm, setIsTestingLlm] = useState(false);
  const {
    hasResumable,
    resumableProgress,
    hasResumableStudy,
    resumableStudyProgress,
    pendingCount,
    studyPlan,
    studyPlanError,
    homeStateLoaded,
  } = useHomeStatus();

  useEffect(() => {
    if (!resetArmed) return;
    const timeoutId = window.setTimeout(() => {
      setResetArmed(false);
      setResetStatus('');
    }, 8000);
    return () => window.clearTimeout(timeoutId);
  }, [resetArmed]);

  useEffect(() => {
    let cancelled = false;
    api.getLlmSettings()
      .then(settings => {
        if (cancelled) return;
        setLlmProvider(settings.provider);
        setLlmModel(settings.model ?? settings.effective_model ?? '');
        setLlmBaseUrl(settings.base_url ?? '');
        setLlmTimeout(settings.timeout_seconds);
        setLlmKeyStatus(settings.api_key_configured ? `Key: ${settings.api_key_source}` : 'Key: none');
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : String(err);
        setLlmStatus('LLM 設定讀取失敗：' + message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function startPlacement(count: number) {
    sessionStorage.setItem('placement_count', String(count));
    sessionStorage.removeItem('placement_resume');
    navigate('/placement');
  }

  function startResume() {
    sessionStorage.setItem('placement_resume', '1');
    navigate('/placement');
  }

  function startStudy(count: number, mode = 'fixed') {
    unlockTtsFromGesture();
    sessionStorage.setItem('study_count', String(count));
    sessionStorage.setItem('study_mode', mode);
    sessionStorage.removeItem('study_resume');
    navigate('/study');
  }

  function startStudyResume() {
    unlockTtsFromGesture();
    sessionStorage.setItem('study_resume', '1');
    navigate('/study');
  }

  function unlockTtsFromGesture() {
    if (typeof window === 'undefined' || !window.speechSynthesis) return;
    try {
      window.speechSynthesis.resume();
      localStorage.setItem('tts_unlocked', '1');
    } catch {
      // Browser support varies; StudyPage can still unlock via the speaker button.
    }
  }

  async function handleResetProgress() {
    if (isResetting) return;
    if (!resetArmed) {
      setResetArmed(true);
      setResetStatus('再按一次會清除盤點、複習與 FSRS 進度。單字資料會保留。');
      return;
    }

    setIsResetting(true);
    setResetStatus('重置中…');
    try {
      await api.resetProgress();
      
      await db.transaction('rw', [
        db.placement_sessions, db.placement_items, db.placement_cards,
        db.placement_audits, db.placement_audit_items,
        db.study_sessions, db.study_items, db.pending_events
      ], async () => {
        await db.placement_sessions.clear();
        await db.placement_items.clear();
        await db.placement_cards.clear();
        await db.placement_audits.clear();
        await db.placement_audit_items.clear();
        await db.study_sessions.clear();
        await db.study_items.clear();
        await db.pending_events.clear();
      });

      sessionStorage.removeItem('placement_resume');
      sessionStorage.removeItem('study_resume');

      setResetArmed(false);
      setResetStatus('進度已重置。');
      window.location.reload();
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      setResetStatus('重置失敗：' + message);
    } finally {
      setIsResetting(false);
    }
  }

  function modelPlaceholder(provider: LlmProvider) {
    if (provider === 'openrouter') return 'openrouter/owl-alpha';
    return 'gemini-2.5-flash';
  }

  function providerLabel(provider: LlmProvider) {
    return LLM_PROVIDER_OPTIONS.find(option => option.value === provider)?.label ?? provider;
  }

  function buildLlmSettingsPayload(): LlmSettingsUpdate {
    return {
      provider: llmProvider,
      model: llmModel.trim() || null,
      base_url: llmBaseUrl.trim() || null,
      api_key: llmApiKey.trim() || null,
      timeout_seconds: llmTimeout,
    };
  }

  async function handleSaveLlmSettings() {
    if (isSavingLlm || isTestingLlm) return;
    setIsSavingLlm(true);
    setLlmStatus('LLM 設定儲存中…');
    try {
      const settings = await api.updateLlmSettings(buildLlmSettingsPayload());
      setLlmApiKey('');
      setLlmKeyStatus(settings.api_key_configured ? `Key: ${settings.api_key_source}` : 'Key: none');
      setLlmStatus('LLM 設定已儲存。');
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      setLlmStatus('LLM 設定儲存失敗：' + message);
    } finally {
      setIsSavingLlm(false);
    }
  }

  async function handleTestLlmSettings() {
    if (isTestingLlm || isSavingLlm) return;
    setIsTestingLlm(true);
    setLlmStatus('LLM 設定儲存並測試中…');
    try {
      const settings = await api.updateLlmSettings(buildLlmSettingsPayload());
      setLlmApiKey('');
      setLlmKeyStatus(settings.api_key_configured ? `Key: ${settings.api_key_source}` : 'Key: none');
      const result = await api.testLlmSettings();
      if (result.ok) {
        setLlmStatus(`LLM 測試成功：${result.provider ?? ''} ${result.model ?? ''}`.trim());
      } else {
        setLlmStatus('LLM 測試失敗：' + (result.error ?? 'unknown error'));
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      setLlmStatus('LLM 測試失敗：' + message);
    } finally {
      setIsTestingLlm(false);
    }
  }

  const mainAction = homeStateLoaded ? buildMainAction({
    hasResumableStudy,
    resumableStudyProgress,
    hasResumable,
    resumableProgress,
    studyPlan,
    studyPlanError,
    startStudy,
    startPlacement,
    startStudyResume,
    startResume,
    navigateToImport: () => navigate('/import'),
    navigateToMistakes: () => navigate('/mistakes'),
    navigateToSummary: () => {
      if (studyPlan?.pending_adjudication_session_id) {
        sessionStorage.setItem(STUDY_SUMMARY_SESSION_STORAGE_KEY, studyPlan.pending_adjudication_session_id);
      }
      navigate('/study/summary');
    },
    reloadPage: () => window.location.reload(),
  }) : null;

  return (
    <div className="full-screen">
      {pendingCount !== null && pendingCount > 0 && (
        <header className="nav-header home-sync-header">
          <span className="home-sync-status">
            <span className="pending-dot" />
            {pendingCount} 待同步
          </span>
        </header>
      )}

      <main className="page">
        <div className="page-content page-content-home">

          {mainAction ? (
            <section className={`home-action-card home-action-${mainAction.state}`}>
              <div className="home-action-copy">
                <p className="home-action-kicker">現在該做什麼</p>
                <h1>{mainAction.title}</h1>
                <p>{mainAction.detail}</p>
              </div>
              <button
                id={`home-primary-${mainAction.state}`}
                className="btn btn-primary btn-full btn-lg"
                onClick={mainAction.onClick}
              >
                {mainAction.button}
                <MaterialSymbol name="arrow_forward" className="btn-inline-icon" />
              </button>
            </section>
          ) : (
            <section className="home-action-card" aria-busy="true">
              <div className="home-action-copy">
                <div className="skeleton home-skeleton-kicker" />
                <div className="skeleton home-skeleton-title" />
                <div className="skeleton home-skeleton-copy" />
              </div>
              <div className="skeleton home-skeleton-action" />
            </section>
          )}

          {mainAction?.state === 'placement' && (
            <section className="section-block">
              <h2 className="section-heading">盤點題數</h2>
              <div className="study-option-grid">
                {PLACEMENT_OPTIONS.map(opt => (
                  <button
                    key={opt.count}
                    id={`placement-btn-${opt.count}`}
                    className="study-option-card"
                    onClick={() => startPlacement(opt.count)}
                  >
                    <span>{opt.label}</span>
                  </button>
                ))}
              </div>
            </section>
          )}

          {mainAction?.state === 'study' && (
            <section className="section-block">
              <h2 className="section-heading">本輪題數</h2>
              <div className="study-option-grid">
              {STUDY_OPTIONS.map(opt => (
                <button
                  key={opt.label}
                  id={`study-btn-${opt.count}`}
                  className="study-option-card"
                  onClick={() => startStudy(opt.count)}
                >
                  <span>{opt.label}</span>
                </button>
              ))}
              </div>
            </section>
          )}

          {studyPlan && (
            <section className="home-status-strip" aria-label="學習狀態">
              <div>
                <span>{studyPlan.due_count}</span>
                <small>到期複習</small>
              </div>
              <div>
                <span>{studyPlan.pending_new_count ?? studyPlan.remaining_new_cards}</span>
                <small>待學佇列</small>
              </div>
              <div>
                <span>{nextDueLabel(studyPlan)}</span>
                <small>下次到期</small>
              </div>
            </section>
          )}

          {/* CSV Import section */}
          <section className="section-block section-block-muted">
            <h2 className="section-heading">
              工具
            </h2>
            <div className="section-list">
              <button
                id="import-csv-btn"
                className="list-item-btn"
                onClick={() => navigate('/import')}
              >
                <span className="home-tool-title">
                  匯入單字檔案
                </span>
                <span className="home-tool-action">
                  進入
                </span>
              </button>
            </div>
          </section>

          {/* Settings & Reset section */}
          <section className="section-block">
            <h2 className="section-heading">
              設定
            </h2>
            <div className="home-settings">
              {/* Theme Settings inline */}
              <div className="home-setting-row">
                <span className="home-setting-label">介面主題</span>
                <button
                  id="toggle-theme-btn"
                  className="btn btn-secondary btn-sm"
                  onClick={onToggleTheme}
                  aria-label={theme === 'dark' ? '切換到亮色模式' : '切換到暗色模式'}
                >
                  切換至 {theme === 'dark' ? '亮色模式' : '暗色模式'}
                </button>
              </div>

              <div className="home-setting-row home-setting-row-bordered home-setting-row-stack">
                <div className="home-llm-panel">
                  <div className="home-llm-panel-header">
                    <div>
                      <div className="home-setting-label">LLM 批改</div>
                      <div className="home-setting-description">{llmKeyStatus}</div>
                    </div>
                    <div className="home-llm-actions">
                      <button
                        className="btn btn-secondary btn-sm"
                        onClick={handleTestLlmSettings}
                        disabled={isTestingLlm || isSavingLlm}
                      >
                        {isTestingLlm ? '測試中…' : '儲存並測試'}
                      </button>
                      <button
                        className="btn btn-primary btn-sm"
                        onClick={handleSaveLlmSettings}
                        disabled={isSavingLlm || isTestingLlm}
                      >
                        {isSavingLlm ? '儲存中…' : '儲存'}
                      </button>
                    </div>
                  </div>

                  <div className="home-llm-current">
                    <span>{providerLabel(llmProvider)}</span>
                    <strong>{llmModel.trim() || modelPlaceholder(llmProvider)}</strong>
                  </div>

                  <div className="home-llm-grid">
                    <label className="home-llm-field">
                      <span>Provider</span>
                      <select
                        className="home-form-control"
                        value={llmProvider}
                        onChange={event => {
                          const provider = event.target.value as LlmProvider;
                          setLlmProvider(provider);
                          if (provider === 'openai_compatible' && !llmBaseUrl.trim()) {
                            setLlmBaseUrl(DEFAULT_OPENAI_COMPATIBLE_BASE_URL);
                          }
                        }}
                      >
                        {LLM_PROVIDER_OPTIONS.map(option => (
                          <option key={option.value} value={option.value}>{option.label}</option>
                        ))}
                      </select>
                    </label>
                    <label className="home-llm-field">
                      <span>Model</span>
                      <input
                        className="home-form-control"
                        value={llmModel}
                        onChange={event => setLlmModel(event.target.value)}
                        placeholder={modelPlaceholder(llmProvider)}
                      />
                    </label>
                  </div>

                  <details className="home-llm-advanced">
                    <summary>
                      <span>連線設定</span>
                      <MaterialSymbol name="chevron_right" />
                    </summary>
                    <div className="home-llm-grid">
                      {llmProvider === 'openai_compatible' && (
                        <label className="home-llm-field home-llm-field-wide">
                          <span>Base URL</span>
                          <input
                            className="home-form-control"
                            value={llmBaseUrl}
                            onChange={event => setLlmBaseUrl(event.target.value)}
                            placeholder={DEFAULT_OPENAI_COMPATIBLE_BASE_URL}
                          />
                        </label>
                      )}
                      <label className="home-llm-field">
                        <span>API Key</span>
                        <input
                          className="home-form-control"
                          type="password"
                          value={llmApiKey}
                          onChange={event => setLlmApiKey(event.target.value)}
                          placeholder="保留現有 key"
                        />
                      </label>
                      <label className="home-llm-field">
                        <span>Timeout</span>
                        <input
                          className="home-form-control"
                          type="number"
                          min={5}
                          max={180}
                          value={llmTimeout}
                          onChange={event => setLlmTimeout(Number(event.target.value) || 45)}
                        />
                      </label>
                    </div>
                  </details>
                </div>
              </div>
              {llmStatus && (
                <div aria-live="polite" className="home-reset-status">
                  {llmStatus}
                </div>
              )}

              {/* Reset progress */}
              <div className="home-setting-row home-setting-row-bordered">
                <div>
                  <div className="home-setting-label">重置所有進度</div>
                  <div className="home-setting-description">清空所有盤點、學習與 FSRS 記錄</div>
                </div>
                <button
                  id="reset-progress-btn"
                  onClick={handleResetProgress}
                  disabled={isResetting}
                  className="btn btn-secondary btn-sm text-error-static"
                >
                  {isResetting ? '重置中…' : resetArmed ? '再次點擊確認' : '重置'}
                </button>
              </div>
              {resetStatus && (
                <div
                  aria-live="polite"
                  className={`home-reset-status ${resetStatus.startsWith('重置失敗') ? 'text-error-static' : ''}`}
                >
                  {resetStatus}
                </div>
              )}
            </div>
          </section>

          {/* Bottom links */}
          <div className="home-footer-links">
            <button
              id="mistakes-link"
              className="btn btn-ghost home-footer-link"
              onClick={() => navigate('/mistakes')}
            >
              錯題與 Podcast 匯出
            </button>
          </div>

        </div>
      </main>
    </div>
  );
}
