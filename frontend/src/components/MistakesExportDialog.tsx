import type { RefObject } from 'react';

import { MaterialSymbol } from './MaterialSymbol';

const LIMIT_OPTIONS = [
  { id: 'top_8', value: '8', label: '前 8 筆' },
  { id: 'top_12', value: '12', label: '前 12 筆' },
  { id: 'top_15', value: '15', label: '前 15 筆' },
  { id: 'all', value: 'all', label: '全部' },
  { id: 'custom', value: 'custom', label: '自訂' },
];

interface MistakesExportDialogProps {
  dialogRef: RefObject<HTMLDivElement | null>;
  closeRef: RefObject<HTMLButtonElement | null>;
  filterType: string;
  setFilterType: (value: string) => void;
  limitType: string;
  setLimitType: (value: string) => void;
  customLimit: string;
  setCustomLimit: (value: string) => void;
  preview: string;
  isExporting: boolean;
  copyFeedback: string;
  onClose: () => void;
  onCopy: () => void;
  onDownload: () => void;
}

export function MistakesExportDialog({
  dialogRef,
  closeRef,
  filterType,
  setFilterType,
  limitType,
  setLimitType,
  customLimit,
  setCustomLimit,
  preview,
  isExporting,
  copyFeedback,
  onClose,
  onCopy,
  onDownload,
}: MistakesExportDialogProps) {
  return (
    <div className="full-screen export-modal-backdrop">
      <button
        type="button"
        className="export-modal-dismiss"
        onClick={onClose}
        aria-label="關閉 Podcast 匯出視窗"
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="export-modal-title"
        className="export-modal"
      >
        <div className="export-modal-header">
          <h2 id="export-modal-title" className="export-modal-title">
            Podcast 複習素材
          </h2>
          <button
            ref={closeRef}
            onClick={onClose}
            className="icon-button"
            aria-label="關閉"
          >
            <MaterialSymbol name="close" />
          </button>
        </div>

        <div className="export-modal-content">
          <div>
            <label htmlFor="export-filter-type" className="form-label">
              匯出內容範圍
            </label>
            <select
              id="export-filter-type"
              name="exportFilterType"
              value={filterType}
              onChange={event => setFilterType(event.target.value)}
              className="form-control"
            >
              <option value="today">今日錯題</option>
              <option value="recent_7_days">近 7 天錯題 (Again / Hard)</option>
              <option value="recent_30_days">近 30 天錯題 (Again / Hard)</option>
            </select>
          </div>

          <div>
            <div className="form-label">輸出數量限制</div>
            <div
              role="group"
              aria-label="Podcast 輸出數量限制"
              className="export-limit-options"
            >
              {LIMIT_OPTIONS.map(option => (
                <button
                  key={option.id}
                  id={`btn-export-limit-${option.id}`}
                  type="button"
                  aria-pressed={limitType === option.value}
                  onClick={() => setLimitType(option.value)}
                  className="export-limit-option"
                >
                  {option.label}
                </button>
              ))}
            </div>
            {limitType === 'custom' && (
              <div>
                <label htmlFor="export-custom-limit-input" className="sr-only">
                  自訂輸出數量
                </label>
                <input
                  type="number"
                  id="export-custom-limit-input"
                  name="exportCustomLimit"
                  value={customLimit}
                  onChange={event => setCustomLimit(event.target.value)}
                  autoComplete="off"
                  inputMode="numeric"
                  min={1}
                  className="form-control export-custom-limit"
                />
              </div>
            )}
          </div>

          <div>
            <label htmlFor="export-preview-area" className="form-label">
              內容預覽
            </label>
            <div className="sr-only" role="status" aria-live="polite">
              {isExporting ? '預覽載入中…' : copyFeedback}
            </div>
            <textarea
              id="export-preview-area"
              name="exportPreview"
              readOnly
              value={isExporting ? '預覽載入中…' : preview}
              className="form-control export-preview"
            />
          </div>
        </div>

        <div className="export-modal-footer">
          <button
            id="btn-export-copy"
            onClick={onCopy}
            disabled={isExporting || !preview.trim()}
            className="btn btn-secondary"
          >
            <span aria-live="polite">{copyFeedback || '複製到剪貼簿'}</span>
          </button>
          <button
            id="btn-export-download"
            onClick={onDownload}
            disabled={isExporting || !preview.trim()}
            className="btn btn-primary"
          >
            下載檔案
          </button>
        </div>
      </div>
    </div>
  );
}
