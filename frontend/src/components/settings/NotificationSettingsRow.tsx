import { useEffect, useState } from 'react';
import { api } from '../../services/api';

export function NotificationSettingsRow() {
  const [minimumDueCount, setMinimumDueCount] = useState('');
  const [discordConfigured, setDiscordConfigured] = useState(false);
  const [status, setStatus] = useState('');
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.getNotificationSettings()
      .then(settings => {
        if (cancelled) return;
        setMinimumDueCount(String(settings.minimum_due_count));
        setDiscordConfigured(settings.discord_configured);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setStatus('通知設定讀取失敗：' + (err instanceof Error ? err.message : String(err)));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function saveSettings() {
    if (isSaving || !minimumDueCount) return;
    setIsSaving(true);
    setStatus('通知設定儲存中…');
    try {
      const settings = await api.updateNotificationSettings(Number(minimumDueCount));
      setMinimumDueCount(String(settings.minimum_due_count));
      setDiscordConfigured(settings.discord_configured);
      setStatus('通知設定已儲存。');
    } catch (err: unknown) {
      setStatus('通知設定儲存失敗：' + (err instanceof Error ? err.message : String(err)));
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <>
      <div className="home-setting-row home-setting-row-bordered home-setting-row-stack">
        <div className="home-settings-panel home-notification-panel">
          <div className="home-notification-header">
            <div>
              <div className="home-setting-label">Discord 複習通知</div>
              <div className="home-setting-description">{discordConfigured ? 'Webhook 已設定' : '尚未設定 Discord Webhook'}</div>
            </div>
            <button className="btn btn-secondary btn-sm home-compact-action" onClick={saveSettings} disabled={isSaving || !minimumDueCount}>
              {isSaving ? '儲存中…' : '儲存'}
            </button>
          </div>
          <label className="home-notification-threshold">
            <span className="home-llm-field-label">通知門檻</span>
            <span className="home-inline-number">
              待複習題數達到
              <input className="home-form-control" type="number" min={1} max={1000} value={minimumDueCount} onChange={event => setMinimumDueCount(event.target.value)} />
              題時通知
            </span>
          </label>
        </div>
      </div>
      {status && <div aria-live="polite" className="home-reset-status">{status}</div>}
    </>
  );
}
