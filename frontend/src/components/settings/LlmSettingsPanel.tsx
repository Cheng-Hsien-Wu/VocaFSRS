import { useEffect, useMemo, useState } from 'react';
import { MaterialSymbol } from '../MaterialSymbol';
import {
  api,
  type ConcreteLlmProvider,
  type LlmFallbackRoute,
  type LlmProvider,
  type LlmSettingsDto,
  type LlmSettingsUpdate,
} from '../../services/api';

const DEFAULT_OPENAI_COMPATIBLE_BASE_URL = 'https://generativelanguage.googleapis.com/v1beta/openai/chat/completions';
const MAX_FALLBACK_ROUTES = 5;
const PROVIDER_OPTIONS: { value: LlmProvider; label: string }[] = [
  { value: 'auto', label: '自動選擇' },
  { value: 'gemini', label: 'Gemini Native' },
  { value: 'openai_compatible', label: 'OpenAI-compatible' },
  { value: 'openrouter', label: 'OpenRouter' },
];

interface LlmSettingsDraft {
  provider: LlmProvider;
  model: string;
  baseUrl: string;
  apiKey: string;
  timeoutSeconds: string;
  fallbackRoutes: LlmFallbackRoute[];
  batchSize: string;
  maxConcurrency: string;
}

function providerLabel(provider: LlmProvider) {
  return PROVIDER_OPTIONS.find(option => option.value === provider)?.label ?? provider;
}

function localKeyProvider(provider: LlmProvider): ConcreteLlmProvider {
  return provider === 'auto' ? 'openrouter' : provider;
}

function providersShareLocalKey(left: LlmProvider, right: LlmProvider) {
  return localKeyProvider(left) === localKeyProvider(right);
}

function draftFromSettings(settings: LlmSettingsDto): LlmSettingsDraft {
  return {
    provider: settings.provider,
    model: settings.model ?? '',
    baseUrl: settings.base_url ?? '',
    apiKey: '',
    timeoutSeconds: String(settings.timeout_seconds),
    fallbackRoutes: settings.provider === 'auto' ? [] : settings.fallback_routes,
    batchSize: String(settings.batch_size),
    maxConcurrency: String(settings.max_concurrency),
  };
}

export function LlmSettingsPanel() {
  const [settings, setSettings] = useState<LlmSettingsDto | null>(null);
  const [draft, setDraft] = useState<LlmSettingsDraft | null>(null);
  const [status, setStatus] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [isTesting, setIsTesting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.getLlmSettings()
      .then(nextSettings => {
        if (cancelled) return;
        setSettings(nextSettings);
        setDraft(draftFromSettings(nextSettings));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setStatus('LLM 設定讀取失敗：' + (err instanceof Error ? err.message : String(err)));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const providerReadiness = draft?.provider === 'auto'
    ? null
    : settings?.provider_readiness.find(item => item.provider === draft?.provider);
  const effectiveModel = draft?.model.trim() || providerReadiness?.effective_model || '';
  const fallbackProviderOptions = useMemo(() => {
    if (!settings || !draft || draft.provider === 'auto') return [];
    return settings.provider_readiness.filter(item => (
      item.fallback_available
      || item.provider === draft.provider
      || draft.fallbackRoutes.some(route => route.provider === item.provider)
    ));
  }, [draft, settings]);
  function updateDraft(patch: Partial<LlmSettingsDraft>) {
    setDraft(current => current ? { ...current, ...patch } : current);
  }

  function selectProvider(provider: LlmProvider) {
    if (!draft) return;
    const fallbackRoutes = provider === 'auto'
      ? []
      : draft.fallbackRoutes.filter(route => (
        route.provider === provider
        || settings?.provider_readiness.some(item => (
          item.provider === route.provider && item.fallback_available
        ))
      ));
    updateDraft({
      provider,
      model: '',
      baseUrl: provider === 'openai_compatible' ? DEFAULT_OPENAI_COMPATIBLE_BASE_URL : '',
      apiKey: providersShareLocalKey(draft.provider, provider) ? draft.apiKey : '',
      fallbackRoutes,
    });
  }

  function addFallbackRoute() {
    if (!draft || draft.provider === 'auto' || draft.fallbackRoutes.length >= MAX_FALLBACK_ROUTES) return;
    updateDraft({
      fallbackRoutes: [...draft.fallbackRoutes, { provider: draft.provider, model: '' }],
    });
  }

  function updateFallbackRoute(index: number, patch: Partial<LlmFallbackRoute>) {
    if (!draft) return;
    updateDraft({
      fallbackRoutes: draft.fallbackRoutes.map((route, routeIndex) => (
        routeIndex === index ? { ...route, ...patch } : route
      )),
    });
  }

  function moveFallbackRoute(index: number, direction: -1 | 1) {
    if (!draft) return;
    const nextIndex = index + direction;
    if (nextIndex < 0 || nextIndex >= draft.fallbackRoutes.length) return;
    const routes = [...draft.fallbackRoutes];
    [routes[index], routes[nextIndex]] = [routes[nextIndex], routes[index]];
    updateDraft({ fallbackRoutes: routes });
  }

  function removeFallbackRoute(index: number) {
    if (!draft) return;
    updateDraft({ fallbackRoutes: draft.fallbackRoutes.filter((_, routeIndex) => routeIndex !== index) });
  }

  function buildPayload(): LlmSettingsUpdate {
    if (!draft) throw new Error('LLM settings are not loaded');
    const fallbackRoutes = draft.fallbackRoutes.map(route => ({ ...route, model: route.model.trim() }));
    if (fallbackRoutes.some(route => !route.model)) {
      throw new Error('每個備援項目都必須填寫模型');
    }
    const routeKeys = fallbackRoutes.map(route => `${route.provider}\u0000${route.model}`);
    if (new Set(routeKeys).size !== routeKeys.length) {
      throw new Error('相同服務與模型不能重複');
    }
    if (
      draft.provider !== 'auto'
      && fallbackRoutes.some(route => route.provider === draft.provider && route.model === effectiveModel)
    ) {
      throw new Error('備援模型不能和主要模型相同');
    }
    return {
      provider: draft.provider,
      model: draft.provider === 'auto' ? null : draft.model.trim() || null,
      base_url: draft.provider === 'openai_compatible' ? draft.baseUrl.trim() || null : null,
      api_key: draft.apiKey.trim() || null,
      clear_api_key: Boolean(
        settings?.api_key_source === 'local'
        && !providersShareLocalKey(draft.provider, settings.provider)
        && !draft.apiKey.trim()
      ),
      timeout_seconds: Number(draft.timeoutSeconds),
      fallback_routes: draft.provider === 'auto' ? [] : fallbackRoutes,
      batch_size: Number(draft.batchSize),
      max_concurrency: Number(draft.maxConcurrency),
    };
  }

  function applySettings(nextSettings: LlmSettingsDto) {
    setSettings(nextSettings);
    setDraft(draftFromSettings(nextSettings));
  }

  async function saveSettings() {
    if (!draft || isSaving || isTesting) return;
    setIsSaving(true);
    setStatus('LLM 設定儲存中…');
    try {
      applySettings(await api.updateLlmSettings(buildPayload()));
      setStatus('LLM 設定已儲存。');
    } catch (err: unknown) {
      setStatus('LLM 設定儲存失敗：' + (err instanceof Error ? err.message : String(err)));
    } finally {
      setIsSaving(false);
    }
  }

  async function testSettings() {
    if (!draft || isSaving || isTesting) return;
    setIsTesting(true);
    setStatus('LLM 設定儲存並測試中…');
    try {
      applySettings(await api.updateLlmSettings(buildPayload()));
      const result = await api.testLlmSettings();
      setStatus(result.ok
        ? `LLM 測試成功：${result.provider ?? ''} ${result.model ?? ''}`.trim()
        : 'LLM 測試失敗：' + (result.error ?? 'unknown error'));
    } catch (err: unknown) {
      setStatus('LLM 測試失敗：' + (err instanceof Error ? err.message : String(err)));
    } finally {
      setIsTesting(false);
    }
  }

  const keyStatus = settings?.api_key_configured
    ? `API Key · ${settings.api_key_source}`
    : '尚未設定 API Key';

  return (
    <>
      <div className="home-setting-row home-setting-row-bordered home-setting-row-stack">
        <div className="home-settings-panel home-llm-panel">
          <div className="home-llm-panel-header">
            <div>
              <div className="home-setting-label">LLM 批改</div>
              <div className="home-setting-description">{draft ? keyStatus : '讀取設定中…'}</div>
            </div>
            <div className="home-llm-actions">
              <button className="btn btn-secondary btn-sm" onClick={testSettings} disabled={!draft || isTesting || isSaving}>
                {isTesting ? '測試中…' : '儲存並測試'}
              </button>
              <button className="btn btn-primary btn-sm" onClick={saveSettings} disabled={!draft || isSaving || isTesting}>
                {isSaving ? '儲存中…' : '儲存'}
              </button>
            </div>
          </div>

          {draft && (
            <>
              <div className="home-llm-grid">
                <label className="home-llm-field">
                  <span>主要服務</span>
                  <select className="home-form-control" value={draft.provider} onChange={event => selectProvider(event.target.value as LlmProvider)}>
                    {PROVIDER_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}
                  </select>
                </label>
                {draft.provider === 'auto' ? (
                  <div className="home-llm-field">
                    <span>主要模型</span>
                    <div className="home-setting-description">依可用服務使用各自預設模型</div>
                  </div>
                ) : (
                  <label className="home-llm-field">
                    <span>主要模型</span>
                    <input className="home-form-control" value={draft.model} onChange={event => updateDraft({ model: event.target.value })} placeholder={effectiveModel} />
                  </label>
                )}
              </div>

              {draft.provider !== 'auto' && (
                <fieldset className="home-llm-fallback">
                  <legend>備援模型</legend>
                  <div className="home-llm-fallback-header">
                    <p>主要模型失敗時依上到下嘗試；同一服務可加入多個模型。</p>
                    <button
                      type="button"
                      className="btn btn-secondary btn-sm home-compact-action"
                      onClick={addFallbackRoute}
                      disabled={draft.fallbackRoutes.length >= MAX_FALLBACK_ROUTES || fallbackProviderOptions.length === 0}
                    >
                      新增
                    </button>
                  </div>
                  {draft.fallbackRoutes.length > 0 ? (
                    <div className="home-llm-route-list">
                      {draft.fallbackRoutes.map((route, index) => (
                        <div className="home-llm-route" key={`${index}-${route.provider}`}>
                          <span className="home-llm-route-order">{index + 1}</span>
                          <label className="home-llm-field">
                            <span>服務</span>
                            <select
                              className="home-form-control"
                              value={route.provider}
                              onChange={event => updateFallbackRoute(index, { provider: event.target.value as ConcreteLlmProvider })}
                            >
                              {fallbackProviderOptions.map(option => (
                                <option key={option.provider} value={option.provider}>{providerLabel(option.provider)}</option>
                              ))}
                            </select>
                          </label>
                          <label className="home-llm-field home-llm-route-model">
                            <span>模型</span>
                            <input
                              className="home-form-control"
                              value={route.model}
                              onChange={event => updateFallbackRoute(index, { model: event.target.value })}
                              placeholder={settings?.provider_readiness.find(item => item.provider === route.provider)?.effective_model}
                            />
                          </label>
                          <div className="home-llm-route-actions">
                            <button type="button" className="home-icon-button" onClick={() => moveFallbackRoute(index, -1)} disabled={index === 0} aria-label={`將備援模型 ${index + 1} 上移`} title="上移">
                              <MaterialSymbol name="chevron_right" className="home-icon-up" />
                            </button>
                            <button type="button" className="home-icon-button" onClick={() => moveFallbackRoute(index, 1)} disabled={index === draft.fallbackRoutes.length - 1} aria-label={`將備援模型 ${index + 1} 下移`} title="下移">
                              <MaterialSymbol name="chevron_right" className="home-icon-down" />
                            </button>
                            <button type="button" className="home-icon-button text-error-static" onClick={() => removeFallbackRoute(index)} aria-label={`刪除備援模型 ${index + 1}`} title="刪除">
                              <MaterialSymbol name="close" />
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="home-setting-description">尚未設定備援模型</div>
                  )}
                </fieldset>
              )}

              <details className="home-llm-advanced">
                <summary><span>連線設定</span><MaterialSymbol name="chevron_right" /></summary>
                <div className="home-llm-grid home-llm-advanced-grid">
                  {draft.provider === 'openai_compatible' && (
                    <label className="home-llm-field home-llm-field-wide">
                      <span>Base URL</span>
                      <input className="home-form-control" value={draft.baseUrl} onChange={event => updateDraft({ baseUrl: event.target.value })} placeholder={DEFAULT_OPENAI_COMPATIBLE_BASE_URL} />
                    </label>
                  )}
                  <label className="home-llm-field">
                    <span>{draft.provider === 'auto' ? 'Auto 第一順位 API Key' : 'API Key'}</span>
                    <input className="home-form-control" type="password" value={draft.apiKey} onChange={event => updateDraft({ apiKey: event.target.value })} placeholder="保留現有 key" />
                  </label>
                  <NumberField label="Timeout（秒）" value={draft.timeoutSeconds} min={5} max={180} onChange={value => updateDraft({ timeoutSeconds: value })} />
                  <NumberField label="每批題數" value={draft.batchSize} min={1} max={50} onChange={value => updateDraft({ batchSize: value })} />
                  <NumberField label="同時請求" value={draft.maxConcurrency} min={1} max={4} onChange={value => updateDraft({ maxConcurrency: value })} />
                </div>
              </details>
            </>
          )}
        </div>
      </div>
      {status && <div aria-live="polite" className="home-reset-status">{status}</div>}
    </>
  );
}

function NumberField({ label, value, min, max, onChange }: {
  label: string;
  value: string;
  min: number;
  max: number;
  onChange: (value: string) => void;
}) {
  return (
    <label className="home-llm-field">
      <span>{label}</span>
      <input className="home-form-control" type="number" min={min} max={max} value={value} onChange={event => onChange(event.target.value)} />
    </label>
  );
}
