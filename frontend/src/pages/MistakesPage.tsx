import { useCallback, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { formatTaipeiDateTime } from '../utils/datetime';
import { MaterialSymbol } from '../components/MaterialSymbol';
import { useMistakesList } from '../hooks/useMistakesData';
import { useMistakesExport } from '../hooks/useMistakesExport';
import { useModalFocus } from '../hooks/useModalFocus';
import { MistakesExportDialog } from '../components/MistakesExportDialog';

export default function MistakesPage() {
  const navigate = useNavigate();
  const exportTriggerRef = useRef<HTMLButtonElement | null>(null);
  const exportCloseRef = useRef<HTMLButtonElement | null>(null);
  const exportDialogRef = useRef<HTMLDivElement | null>(null);

  const {
    mistakes,
    totalMistakes,
    mistakesPage,
    days,
    setDays,
    ratingFilter,
    setRatingFilter,
    repeatedLapses,
    setRepeatedLapses,
    expandedMistakeWord,
    setExpandedMistakeWord,
    isLoadingMistakes,
    loadMistakes,
  } = useMistakesList();

  const {
    showExportModal,
    setShowExportModal,
    exportFilterType,
    setExportFilterType,
    exportLimitType,
    setExportLimitType,
    exportCustomLimit,
    setExportCustomLimit,
    exportPreview,
    isExporting,
    copyFeedback,
    handleCopyToClipboard,
    handleDownloadFile,
    openTodayExport,
  } = useMistakesExport();

  const closeExportModal = useCallback(() => {
    setShowExportModal(false);
  }, [setShowExportModal]);

  useModalFocus({
    active: showExportModal,
    containerRef: exportDialogRef,
    initialFocusRef: exportCloseRef,
    returnFocusRef: exportTriggerRef,
    onClose: closeExportModal,
  });

  return (
    <div className="full-screen mistakes-page">
      {/* Header */}
      <div className="mistakes-header">
        <div className="mistakes-header-title">
          <button className="nav-back" onClick={() => navigate('/')} aria-label="回首頁">
            <MaterialSymbol name="arrow_back" />
          </button>
          <h1>數據分析與管理</h1>
        </div>
        <button
          id="btn-trigger-export"
          ref={exportTriggerRef}
          onClick={openTodayExport}
          className="btn btn-primary btn-sm"
        >
          匯出 Podcast
        </button>
      </div>

      <div className="page mistakes-content">
        <div className="mistakes-tab-panel">
            
            {/* Mistakes Filters */}
            <div className="mistakes-filter-panel">
              {/* Day filter buttons */}
              <div role="group" aria-label="錯題時間範圍" className="mistakes-filter-row">
                <span className="mistakes-filter-label">時間範圍:</span>
                {([7, 30, null] as (number | null)[]).map((d) => (
                  <button
                    key={String(d)}
                    id={`filter-mistake-days-${d === null ? 'all' : d}`}
                    type="button"
                    aria-pressed={days === d}
                    onClick={() => setDays(d)}
                    className="mistakes-filter-option"
                  >
                    {d === 7 ? '7 天' : d === 30 ? '30 天' : '全部'}
                  </button>
                ))}
              </div>

              {/* Rating filter buttons */}
              <div role="group" aria-label="錯題狀態篩選" className="mistakes-filter-row">
                <span className="mistakes-filter-label">狀態篩選:</span>
                {['all', 'Again', 'Hard'].map((r) => (
                  <button
                    key={r}
                    id={`filter-mistake-rating-${r}`}
                    type="button"
                    aria-pressed={ratingFilter === r}
                    onClick={() => setRatingFilter(r)}
                    className="mistakes-filter-option"
                  >
                    {r === 'all' ? '全部' : r === 'Again' ? '再試 (Again)' : '猶豫 (Hard)'}
                  </button>
                ))}
              </div>

              <div className="mistakes-checkbox-row">
                <input
	                  type="checkbox"
	                  id="filter-mistake-lapses"
                    name="filterMistakeLapses"
	                  checked={repeatedLapses}
                  onChange={(e) => setRepeatedLapses(e.target.checked)}
                  className="mistakes-checkbox"
                />
                <label htmlFor="filter-mistake-lapses">
                  重複失誤 (lapses ≥ 2)
                </label>
              </div>
            </div>

            {/* Mistakes List */}
            {mistakes.length === 0 ? (
              <div className="empty-state mistakes-empty-state">
                <MaterialSymbol name="check_circle" fill className="empty-state-icon" />
		                <div className="mistakes-empty-title">沒有符合條件的錯題</div>
		                <div className="mistakes-empty-copy">最新一次仍是 Again 或 Hard 的單字會出現在這裡。</div>
              </div>
            ) : (
              <div className="mistakes-list">
                <div className="mistakes-list-count">
	                  共有 {totalMistakes} 個最新仍未穩定的單字
                </div>
                
                {mistakes.map((m) => (
                  <div key={m.english} className="card content-auto mistakes-entry">
                    <button
                      id={`btn-mistake-word-${m.english}`}
                      onClick={() => setExpandedMistakeWord(expandedMistakeWord === m.english ? null : m.english)}
                      className="mistakes-entry-trigger mistakes-entry-trigger-top"
                    >
                      <div className="mistakes-entry-main">
                        <div className="long-text mistakes-entry-term">
                          {m.english}
                        </div>
                        <div className="long-text mistakes-entry-meaning">
                          {m.part_of_speech ? `[${m.part_of_speech}] ` : ''}{m.chinese_meaning}
                        </div>
                      </div>
                      <div className="mistakes-entry-stats">
                        {m.again_count > 0 && <span className="stat-pill stat-pill-again">再試 {m.again_count}</span>}
                        {m.hard_count > 0 && <span className="stat-pill stat-pill-hard">猶豫 {m.hard_count}</span>}
                        {m.lapses >= 2 && <span className="stat-pill stat-pill-lapse">失誤 {m.lapses}</span>}
                      </div>
                    </button>

                    {expandedMistakeWord === m.english && (
                      <div className="mistakes-entry-details">
                        <div className="mistakes-entry-detail-list">
                          
                          {/* Sense hint */}
                          {m.sense_hint && (
                            <div>
                              <div className="mistakes-detail-label">字義提示</div>
                              <div className="mistakes-detail-value">{m.sense_hint}</div>
                            </div>
                          )}

                          {/* Confusion details */}
                          {m.confused_word && (
                            <div>
                              <div className="mistakes-detail-label mistakes-detail-label-spaced">最近一次錯誤混淆</div>
                              <div className="long-text mistakes-confusion-alert">
                                混淆字: {m.confused_word}
                                {m.selected_wrong_meaning && ` (選了：${m.selected_wrong_meaning})`}
                              </div>
                            </div>
                          )}

                          {/* Example Sentences */}
                          {m.example_sentence && (
                            <div>
                              <div className="mistakes-detail-label">例句</div>
                              <div className="long-text mistakes-example">
                                {m.example_sentence}
                              </div>
                              {m.example_translation && (
                                <div className="long-text mistakes-example-translation">
                                  {m.example_translation}
                                </div>
                              )}
                            </div>
                          )}

                          {m.last_review_time && (
                            <div className="mistakes-timestamp">
                              最晚複習時間：{formatTaipeiDateTime(m.last_review_time)}
                            </div>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                ))}

                {/* Mistakes Load More */}
                {mistakes.length < totalMistakes && (
                  <button
	                    id="btn-load-more-mistakes"
	                    onClick={() => {
	                      loadMistakes(false, mistakesPage + 1);
	                    }}
                    className="mistakes-load-more"
                  >
                    {isLoadingMistakes ? '載入中…' : '載入更多'}
                  </button>
                )}
              </div>
            )}
        </div>
      </div>

      {showExportModal && (
        <MistakesExportDialog
          dialogRef={exportDialogRef}
          closeRef={exportCloseRef}
          filterType={exportFilterType}
          setFilterType={setExportFilterType}
          limitType={exportLimitType}
          setLimitType={setExportLimitType}
          customLimit={exportCustomLimit}
          setCustomLimit={setExportCustomLimit}
          preview={exportPreview}
          isExporting={isExporting}
          copyFeedback={copyFeedback}
          onClose={closeExportModal}
          onCopy={handleCopyToClipboard}
          onDownload={handleDownloadFile}
        />
      )}

    </div>
  );
}
