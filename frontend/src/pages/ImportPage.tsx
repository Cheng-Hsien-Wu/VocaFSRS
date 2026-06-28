import { useNavigate } from 'react-router-dom';
import { MaterialSymbol } from '../components/MaterialSymbol';
import {
  IMPORT_DB_FIELDS,
  IMPORT_STEP_LABELS,
  IMPORT_STEP_ORDER,
  useImportWorkflow,
} from '../hooks/useImportWorkflow';

export default function ImportPage() {
  const navigate = useNavigate();
  const {
    step,
    setStep,
    loading,
    error,
    jobId,
    headers,
    mapping,
    setMapping,
    stats,
    previewRows,
    totalRows,
    page,
    filterClass,
    commitSummary,
    handleFileUpload,
    handleAnalyze,
    fetchPreviewRows,
    handleFilterChange,
    handleCommit,
  } = useImportWorkflow();

  const currentStepNumber = IMPORT_STEP_ORDER.indexOf(step) + 1;

  return (
    <div className="full-screen">
      <header className="nav-header">
        <button className="nav-back nav-close-button" onClick={() => {
          if (step === 'mapping') setStep('upload');
          else if (step === 'preview') setStep('mapping');
          else navigate('/');
        }} aria-label="離開匯入">
          <MaterialSymbol name="close" />
        </button>
        <h1 className="nav-title">匯入單字</h1>
        <div className="nav-header-spacer" />
      </header>

      <main className="page">
        <div className="page-content">

          <div className="import-step-list" aria-label="匯入流程">
            {IMPORT_STEP_ORDER.map((stepName, index) => (
              <div key={stepName} className={index + 1 === currentStepNumber ? 'import-step-current' : ''}>
                <span>{index + 1}</span>
                <strong>{IMPORT_STEP_LABELS[stepName]}</strong>
              </div>
            ))}
          </div>

          {error && (
            <div role="alert" className="import-error">
              {error}
            </div>
          )}

          {loading && (
            <div className="full-screen import-loading-overlay" role="status" aria-live="polite">
              <div className="import-loading-content">
                <div className="loading-spinner" aria-hidden="true" />
                <div>處理中，請稍候…</div>
              </div>
            </div>
          )}

          {/* STEP 1: Upload */}
          {step === 'upload' && (
            <div className="import-upload">
              <h2 className="import-page-title">選擇單字檔案</h2>
              <p className="import-page-description">
                請上傳 UTF-8 編碼的 CSV 或 TXT 檔案 (限制 10&nbsp;MB 以內)。TXT 會自動轉成英文與中文兩欄。
              </p>
              <input
                id="import-file"
                name="importFile"
                className="file-input-native"
                type="file"
                accept=".csv,.txt,text/csv,text/plain"
                onChange={handleFileUpload}
              />
              <label htmlFor="import-file" className="btn btn-primary file-input-label">
                <MaterialSymbol name="upload_file" />
                選擇檔案
              </label>

              <div className="import-example-card">
                <div className="import-example-heading">TXT 範例</div>
                <pre>{`abandon 放棄
a board member 委員會/董事會成員
distinct    清楚不同的`}</pre>
                <p>每一行一筆。英文在左，中文在右；中間可用 Tab、兩個以上空白，或普通空白加中文釋義。</p>
              </div>
            </div>
          )}

          {/* STEP 2: Column Mapping */}
          {step === 'mapping' && (
            <div className="import-flow">
              <h2 className="import-section-title">設定欄位對應</h2>
              <p className="import-section-description">
                請為資料指定欄位。打 * 的是必填欄位；單字會匯入到預設詞庫。
              </p>
              
              <div className="import-field-list">
                {IMPORT_DB_FIELDS.map(field => {
                  return (
                    <div key={field.name} className="import-field">
                      <label htmlFor={`mapping-${field.name}`} className="import-field-label">{field.label}</label>
                      <select
                        id={`mapping-${field.name}`}
                        name={`mapping-${field.name}`}
                        className="card import-select"
                        value={mapping[field.name]}
                        onChange={(e) => setMapping({ ...mapping, [field.name]: e.target.value })}
                      >
                        <option value="">-- 未對應 --</option>
                        {headers.map(h => <option key={h} value={h}>{h}</option>)}
                      </select>
                    </div>
                  );
                })}
              </div>

              <button className="btn btn-primary btn-full import-primary-action" onClick={handleAnalyze}>
                開始分析欄位
                <MaterialSymbol name="arrow_forward" className="btn-inline-icon" />
              </button>
            </div>
          )}

          {/* STEP 3: Preview */}
          {step === 'preview' && stats && (
            <div className="import-flow">
              <h2 className="import-section-title">匯入預覽報告</h2>
              
              {/* Stats Grid */}
              <div className="metric-grid metric-grid-compact">
                <div className="card metric-card metric-card-compact">
                  <div className="metric-label">總筆數</div>
	                  <div className="metric-value tabular-nums">{stats.total_rows}</div>
                </div>
                <div className="card metric-card metric-card-compact import-success-metric">
                  <div className="import-success-label">新增單字</div>
	                  <div className="metric-value tabular-nums text-success">{stats.new_cards}</div>
                </div>
                <div className="card metric-card metric-card-compact">
                  <div className="metric-label">重複略過</div>
	                  <div className="metric-value tabular-nums">{stats.skipped_duplicates}</div>
                </div>
                <div className="card metric-card metric-card-compact">
                  <div className="import-info-label">關聯既有卡片</div>
		                  <div className="metric-value tabular-nums text-info">{stats.linked_existing_cards}</div>
                </div>
              </div>

              {/* Rows List with filter */}
              <div className="import-preview-toolbar">
                <label htmlFor="import-preview-filter" className="import-field-label">單字明細預覽 ({totalRows} 筆)</label>
                <select
                  id="import-preview-filter"
                  name="importPreviewFilter"
                  value={filterClass}
                  onChange={(e) => handleFilterChange(e.target.value)}
                  className="import-filter"
                >
                  <option value="">全部狀態</option>
                  <option value="same_term_variant">同詞變體</option>
                  <option value="probable_duplicate">疑似重複</option>
                  <option value="potential_conflict">潛在衝突</option>
                  <option value="exact_duplicate">完全重複</option>
                  <option value="cross_deck_duplicate">既有卡片</option>
                  <option value="potential_ambiguity">潛在歧義 (排除)</option>
                  <option value="multi_meaning_candidate">多義候選</option>
                  <option value="invalid">無效行</option>
                </select>
              </div>

              {/* Table */}
              <div className="import-preview-list">
                {previewRows.map((r, i) => (
                  <div key={i} className="import-preview-row">
                    <div className="import-preview-row-header">
                      <span className="long-text import-preview-term">
                        {r.original_row_data[mapping['english']] || 'N/A'}
                      </span>
                      <span className={`pill import-preview-pill pill-${(r.action === 'skip' || r.action === 'skipped') ? 'secondary' : (r.action === 'reject' || r.action === 'rejected') ? 'danger' : (r.action === 'flag_ambiguous' || r.action === 'flagged_ambiguous') ? 'warning' : 'success'}`}>
                        {r.classification === 'exact_duplicate' ? '完全重複' :
                         r.classification === 'cross_deck_duplicate' ? '既有卡片' :
                         r.classification === 'potential_ambiguity' ? '歧義排除' :
                         r.classification === 'multi_meaning_candidate' ? '多義' :
                         r.classification === 'invalid' ? '無效行' :
                         r.classification === 'probable_duplicate' ? '疑似重複' :
                         r.classification === 'potential_conflict' ? '潛在衝突' :
                         r.classification === 'same_term_variant' ? '同詞變體' : '正常'}
                      </span>
                    </div>
                    <div className="import-preview-meaning">
                      {r.original_row_data[mapping['chinese_meaning']] || 'N/A'} {r.original_row_data[mapping['part_of_speech']] ? `(${r.original_row_data[mapping['part_of_speech']]})` : ''}
                    </div>
                    {r.message && (
                      <div className="import-preview-message">
                        * {r.message}
                      </div>
                    )}
                  </div>
                ))}
                
                {previewRows.length === 0 && (
                  <div className="import-preview-empty">
                    沒有符合篩選條件的預覽行。
                  </div>
                )}
              </div>

              {/* Pagination */}
              <div className="import-pagination">
                <button
                  className="btn btn-ghost btn-sm"
                  disabled={page === 1}
                  onClick={() => fetchPreviewRows(jobId, page - 1, filterClass)}
                >
                  ◀ 上一頁
                </button>
                <span className="import-pagination-label">第 {page} 頁 / 共 {Math.ceil(totalRows / 10) || 1} 頁</span>
                <button
                  className="btn btn-ghost btn-sm"
                  disabled={page * 10 >= totalRows}
                  onClick={() => fetchPreviewRows(jobId, page + 1, filterClass)}
                >
                  下一頁 ▶
                </button>
              </div>

              <button className="btn btn-primary btn-full import-commit-action" onClick={handleCommit}>
                確認提交匯入
              </button>
            </div>
          )}

          {/* STEP 4: Summary */}
          {step === 'summary' && commitSummary && (
            <div className="import-summary">
              <MaterialSymbol name="check_circle" fill className="import-complete-icon" />
              <h2 className="import-page-title">匯入順利完成！</h2>
              <p className="import-page-description">
                單字基礎數據庫已更新，並且已安全應用去重與關聯機制。
              </p>

              <div className="import-summary-stats">
                <div className="import-summary-row">
                  <span>新增卡片筆數：</span>
                  <strong>{commitSummary.new_cards} 筆</strong>
                </div>
                <div className="import-summary-row">
                  <span>關聯至現有卡片：</span>
                  <strong>{commitSummary.linked_existing_cards} 筆</strong>
                </div>
                <div className="import-summary-row">
                  <span>忽略之重複單字：</span>
                  <strong>{commitSummary.skipped_duplicates} 筆</strong>
                </div>
              </div>

              <button className="btn btn-primary btn-full btn-lg" onClick={() => navigate('/')}>
                回到首頁
              </button>
            </div>
          )}

        </div>
      </main>
    </div>
  );
}
