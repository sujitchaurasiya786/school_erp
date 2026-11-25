import React, { useEffect, useMemo, useState } from 'react';
import { apiRequest } from './api';

const STATUS_META = {
  PRESENT: { label: 'Present', btnClass: 'btn-success', outlineClass: 'btn-outline-success' },
  ABSENT: { label: 'Absent', btnClass: 'btn-danger', outlineClass: 'btn-outline-danger' },
  HALF_DAY: { label: 'Half Day', btnClass: 'btn-warning', outlineClass: 'btn-outline-warning' },
  LEAVE: { label: 'Leave', btnClass: 'btn-info', outlineClass: 'btn-outline-info' },
  LATE: { label: 'Late', btnClass: 'btn-secondary', outlineClass: 'btn-outline-secondary' },
};

export default function DailyAttendanceApp({ initialConfig }) {
  const academicYears = initialConfig.academic_years || [];
  const classes = initialConfig.classes || [];
  const canMark = Boolean(initialConfig.can_mark);
  const canCorrect = Boolean(initialConfig.can_correct);

  const [selectedYearId, setSelectedYearId] = useState(
    initialConfig.selected_year_id ? String(initialConfig.selected_year_id) : (academicYears[0]?.id ? String(academicYears[0].id) : '')
  );
  const [selectedClassId, setSelectedClassId] = useState(
    initialConfig.selected_class_id ? String(initialConfig.selected_class_id) : ''
  );
  const [selectedSectionName, setSelectedSectionName] = useState(initialConfig.selected_section_name || '');
  const [selectedDate, setSelectedDate] = useState(initialConfig.selected_date || new Date().toISOString().slice(0, 10));

  const [searchQuery, setSearchQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [rosterPayload, setRosterPayload] = useState(null);
  const [rows, setRows] = useState([]);
  const [alert, setAlert] = useState(null);
  const [showSubmitConfirm, setShowSubmitConfirm] = useState(false);

  // Correction modal state
  const [correctionRow, setCorrectionRow] = useState(null);
  const [correctionStatus, setCorrectionStatus] = useState('PRESENT');
  const [correctionReason, setCorrectionReason] = useState('');
  const [submittingCorrection, setSubmittingCorrection] = useState(false);

  const selectedClassObj = useMemo(
    () => classes.find((c) => String(c.id) === String(selectedClassId)) || null,
    [classes, selectedClassId]
  );

  const sections = useMemo(() => selectedClassObj?.sections || [], [selectedClassObj]);

  useEffect(() => {
    if (selectedClassId && selectedSectionName && selectedYearId && selectedDate) {
      loadRoster();
    } else {
      setRosterPayload(null);
      setRows([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedYearId, selectedClassId, selectedSectionName, selectedDate]);

  async function loadRoster() {
    if (!selectedClassObj || !selectedSectionName) return;
    setLoading(true);
    setAlert(null);
    try {
      const params = new URLSearchParams({
        academic_year: selectedYearId,
        class_name: selectedClassObj.name,
        section_name: selectedSectionName,
        date: selectedDate,
      });
      const data = await apiRequest(`/api/v1/attendance/daily/roster/?${params.toString()}`);
      setRosterPayload(data);
      setRows(data.roster || []);
    } catch (err) {
      setRosterPayload(null);
      setRows([]);
      setAlert({ type: 'danger', message: err.message });
    } finally {
      setLoading(false);
    }
  }

  const availableStatuses = useMemo(() => {
    const keys = ['PRESENT', 'ABSENT', 'HALF_DAY', 'LEAVE'];
    if (rosterPayload?.allow_late_status) {
      keys.push('LATE');
    }
    return keys;
  }, [rosterPayload]);

  const liveCounts = useMemo(() => {
    const counts = { total: rows.length, present: 0, absent: 0, half_day: 0, leave: 0, late: 0 };
    rows.forEach((r) => {
      if (r.status === 'PRESENT') counts.present += 1;
      else if (r.status === 'ABSENT') counts.absent += 1;
      else if (r.status === 'HALF_DAY') counts.half_day += 1;
      else if (r.status === 'LEAVE') counts.leave += 1;
      else if (r.status === 'LATE') counts.late += 1;
    });
    const effective = counts.present + counts.late + 0.5 * counts.half_day;
    counts.percentage = counts.total > 0 ? ((effective / counts.total) * 100).toFixed(1) : '0.0';
    return counts;
  }, [rows]);

  const filteredRows = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(
      (r) =>
        (r.name || '').toLowerCase().includes(q) ||
        (r.admission_number || '').toLowerCase().includes(q) ||
        String(r.roll_number || '').toLowerCase().includes(q)
    );
  }, [rows, searchQuery]);

  const isLocked = Boolean(rosterPayload?.is_locked);
  const isWorking = Boolean(rosterPayload?.is_working);
  const canEdit = canMark && isWorking && !isLocked;

  function updateStudentField(studentId, field, value) {
    if (!canEdit) return;
    setRows((prev) => prev.map((r) => (r.student_id === studentId ? { ...r, [field]: value } : r)));
  }

  function markAll(statusVal) {
    if (!canEdit) return;
    setRows((prev) => prev.map((r) => ({ ...r, status: statusVal })));
  }

  function buildRecordsPayload() {
    return rows.map((r) => ({
      student_id: r.student_id,
      academic_year_id: Number(selectedYearId),
      date: selectedDate,
      class_name: selectedClassObj.name,
      section_name: selectedSectionName,
      status: r.status,
      remarks: r.remarks || '',
    }));
  }

  async function handleSaveDraft() {
    if (!canEdit || rows.length === 0 || saving) return;
    setSaving(true);
    setAlert(null);
    try {
      const res = await apiRequest('/api/v1/attendance/daily/save-draft/', {
        method: 'POST',
        body: JSON.stringify({ records: buildRecordsPayload() }),
      });
      setAlert({ type: 'success', message: `Draft saved for ${res.updated_count} students. You can continue editing before submitting.` });
      await loadRoster();
    } catch (err) {
      setAlert({ type: 'danger', message: err.message });
    } finally {
      setSaving(false);
    }
  }

  async function handleConfirmSubmit() {
    if (!canEdit || rows.length === 0 || saving) return;
    setSaving(true);
    setAlert(null);
    try {
      await apiRequest('/api/v1/attendance/daily/submit/', {
        method: 'POST',
        body: JSON.stringify({
          academic_year_id: Number(selectedYearId),
          class_name: selectedClassObj.name,
          section_name: selectedSectionName,
          date: selectedDate,
          records: buildRecordsPayload(),
        }),
      });
      setShowSubmitConfirm(false);
      setAlert({ type: 'success', message: 'Attendance submitted and locked successfully.' });
      await loadRoster();
    } catch (err) {
      setShowSubmitConfirm(false);
      setAlert({ type: 'danger', message: err.message });
    } finally {
      setSaving(false);
    }
  }

  async function handleSubmitCorrection(e) {
    e.preventDefault();
    if (!correctionRow?.attendance_id || !correctionReason.trim() || submittingCorrection) return;
    setSubmittingCorrection(true);
    try {
      await apiRequest('/api/v1/attendance/corrections/', {
        method: 'POST',
        body: JSON.stringify({
          attendance: correctionRow.attendance_id,
          new_status: correctionStatus,
          reason: correctionReason.trim(),
        }),
      });
      setCorrectionRow(null);
      setCorrectionReason('');
      setAlert({ type: 'success', message: 'Correction request submitted for administrative approval.' });
      await loadRoster();
    } catch (err) {
      setAlert({ type: 'danger', message: err.message });
    } finally {
      setSubmittingCorrection(false);
    }
  }

  return (
    <div>
      {alert && (
        <div className={`alert alert-${alert.type} alert-dismissible fade show shadow-sm mb-3`} role="alert">
          <i className={`ti ${alert.type === 'success' ? 'ti-check' : 'ti-alert-circle'} me-1`} />
          {alert.message}
          <button type="button" className="btn-close" onClick={() => setAlert(null)} aria-label="Close" />
        </div>
      )}

      {/* Filter Card */}
      <div className="card border-0 shadow-sm mb-4">
        <div className="card-body p-3 p-md-4">
          <div className="row g-3 align-items-end">
            <div className="col-md-3">
              <label className="form-label small fw-semibold text-muted">Academic Session</label>
              <select
                className="form-select shadow-sm"
                value={selectedYearId}
                onChange={(e) => setSelectedYearId(e.target.value)}
              >
                {academicYears.map((ay) => (
                  <option key={ay.id} value={ay.id}>
                    {ay.name} {ay.is_current ? '(Current)' : ''}
                  </option>
                ))}
              </select>
            </div>

            <div className="col-md-3">
              <label className="form-label small fw-semibold text-muted">Class</label>
              <select
                className="form-select shadow-sm"
                value={selectedClassId}
                onChange={(e) => {
                  setSelectedClassId(e.target.value);
                  setSelectedSectionName('');
                }}
              >
                <option value="">-- Select Class --</option>
                {classes.map((cl) => (
                  <option key={cl.id} value={cl.id}>
                    {cl.name}
                  </option>
                ))}
              </select>
            </div>

            <div className="col-md-2">
              <label className="form-label small fw-semibold text-muted">Section</label>
              <select
                className="form-select shadow-sm"
                value={selectedSectionName}
                disabled={!selectedClassId}
                onChange={(e) => setSelectedSectionName(e.target.value)}
              >
                <option value="">-- Select --</option>
                {sections.map((sec) => (
                  <option key={sec.id} value={sec.name}>
                    Section {sec.name}
                  </option>
                ))}
              </select>
            </div>

            <div className="col-md-2">
              <label className="form-label small fw-semibold text-muted">Date</label>
              <input
                type="date"
                className="form-control shadow-sm"
                value={selectedDate}
                onChange={(e) => setSelectedDate(e.target.value)}
              />
            </div>

            <div className="col-md-2">
              <button
                type="button"
                className="btn btn-primary w-100 shadow-sm"
                disabled={!selectedClassId || !selectedSectionName || loading}
                onClick={loadRoster}
              >
                {loading ? (
                  <>
                    <span className="spinner-border spinner-border-sm me-1" /> Loading...
                  </>
                ) : (
                  <>
                    <i className="ti ti-refresh me-1" /> Refresh
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Empty prompt */}
      {(!selectedClassId || !selectedSectionName) && (
        <div className="card border-0 shadow-sm">
          <div className="card-body text-center py-5 text-muted">
            <i className="ti ti-checklist fs-1 d-block mb-2 text-primary opacity-50" />
            <h5 className="fw-bold text-dark">Select Class &amp; Section</h5>
            <p className="small mb-0">Choose an academic session, class, section, and date above to load the student roster.</p>
          </div>
        </div>
      )}

      {/* Roster Loaded */}
      {selectedClassId && selectedSectionName && rosterPayload && (
        <>
          {/* Day & Session Status Banner */}
          {!isWorking ? (
            <div className="alert alert-danger d-flex align-items-start gap-2 shadow-sm mb-4">
              <i className="ti ti-alert-triangle fs-4 text-danger mt-1" />
              <div>
                <h6 className="fw-bold mb-1 text-danger">Attendance Recording Blocked (Non-Working Day)</h6>
                <p className="mb-0 small">
                  {rosterPayload.date} is marked as <strong>{rosterPayload.calendar_reason}</strong>. Attendance cannot be recorded unless an administrator creates a Special Day Override.
                </p>
              </div>
            </div>
          ) : (
            <div className={`alert ${isLocked ? 'alert-primary' : 'alert-success'} d-flex flex-wrap align-items-center justify-content-between gap-2 shadow-sm mb-4`}>
              <div className="d-flex align-items-center gap-2">
                <i className={`ti ${isLocked ? 'ti-lock' : 'ti-check-circle'} fs-4`} />
                <div>
                  <strong>{isLocked ? 'Submitted & Locked' : rosterPayload.session_status === 'DRAFT' ? 'Draft Saved (Editable)' : 'Ready to Mark'}</strong>
                  <span className="ms-2 small">
                    {rosterPayload.date} &bull; {rosterPayload.calendar_reason}
                  </span>
                  {isLocked && rosterPayload.submitted_by && (
                    <span className="badge bg-white text-primary border ms-2">
                      Submitted by {rosterPayload.submitted_by}
                    </span>
                  )}
                </div>
              </div>
              <span className="badge bg-light text-dark border">
                Class Attendance: <strong>{liveCounts.percentage}%</strong>
              </span>
            </div>
          )}

          {/* Live Counters */}
          <div className="row g-3 mb-4">
            <div className="col-6 col-md-2">
              <div className="card border-0 shadow-sm text-center py-2">
                <small className="text-muted fw-semibold">Total</small>
                <h4 className="fw-bold mb-0">{liveCounts.total}</h4>
              </div>
            </div>
            <div className="col-6 col-md-2">
              <div className="card border-0 shadow-sm text-center py-2 border-start border-4 border-success">
                <small className="text-success fw-semibold">Present</small>
                <h4 className="fw-bold mb-0 text-success">{liveCounts.present}</h4>
              </div>
            </div>
            <div className="col-6 col-md-2">
              <div className="card border-0 shadow-sm text-center py-2 border-start border-4 border-danger">
                <small className="text-danger fw-semibold">Absent</small>
                <h4 className="fw-bold mb-0 text-danger">{liveCounts.absent}</h4>
              </div>
            </div>
            <div className="col-6 col-md-2">
              <div className="card border-0 shadow-sm text-center py-2 border-start border-4 border-warning">
                <small className="text-warning fw-semibold">Half Day</small>
                <h4 className="fw-bold mb-0 text-warning">{liveCounts.half_day}</h4>
              </div>
            </div>
            <div className="col-6 col-md-2">
              <div className="card border-0 shadow-sm text-center py-2 border-start border-4 border-info">
                <small className="text-info fw-semibold">Leave</small>
                <h4 className="fw-bold mb-0 text-info">{liveCounts.leave}</h4>
              </div>
            </div>
            <div className="col-6 col-md-2">
              <div className="card border-0 shadow-sm text-center py-2 border-start border-4 border-primary">
                <small className="text-primary fw-semibold">Present %</small>
                <h4 className="fw-bold mb-0 text-primary">{liveCounts.percentage}%</h4>
              </div>
            </div>
          </div>

          {/* Roster Table Card */}
          <div className="card border-0 shadow-sm">
            <div className="card-header bg-white border-bottom py-3 d-flex flex-wrap align-items-center justify-content-between gap-2">
              <div className="d-flex align-items-center gap-2 flex-grow-1" style={{ maxWidth: 360 }}>
                <div className="input-group input-group-sm">
                  <span className="input-group-text bg-light border-end-0">
                    <i className="ti ti-search" />
                  </span>
                  <input
                    type="text"
                    className="form-control border-start-0"
                    placeholder="Search by name, roll no, or admission no..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                  />
                </div>
              </div>

              {canEdit && (
                <div className="d-flex align-items-center gap-2">
                  <button type="button" className="btn btn-outline-success btn-sm" onClick={() => markAll('PRESENT')}>
                    <i className="ti ti-check me-1" /> Mark All Present
                  </button>
                  <button type="button" className="btn btn-outline-danger btn-sm" onClick={() => markAll('ABSENT')}>
                    <i className="ti ti-x me-1" /> Mark All Absent
                  </button>
                </div>
              )}
            </div>

            <div className="card-body p-0">
              {filteredRows.length === 0 ? (
                <div className="text-center py-5 text-muted">
                  <p className="mb-0">No enrolled students match your current filter.</p>
                </div>
              ) : (
                <div className="table-responsive">
                  <table className="table table-hover align-middle mb-0">
                    <thead className="table-light text-muted small text-uppercase">
                      <tr>
                        <th className="ps-3" style={{ width: 80 }}>Roll</th>
                        <th>Student</th>
                        <th style={{ width: 100 }}>Overall %</th>
                        <th style={{ minWidth: 320 }}>Status</th>
                        <th style={{ minWidth: 180 }}>Remarks</th>
                        <th className="text-end pe-3" style={{ width: 150 }}>Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredRows.map((row) => (
                        <tr key={row.student_id}>
                          <td className="ps-3 fw-bold">{row.roll_number || '-'}</td>
                          <td>
                            <div className="d-flex align-items-center gap-2">
                              {row.photo_url ? (
                                <img
                                  src={row.photo_url}
                                  alt={row.name}
                                  className="rounded-circle object-fit-cover border"
                                  style={{ width: 34, height: 34 }}
                                />
                              ) : (
                                <div
                                  className="rounded-circle bg-light-primary text-primary d-flex align-items-center justify-content-center fw-bold border"
                                  style={{ width: 34, height: 34, fontSize: 13 }}
                                >
                                  {(row.name || 'S').charAt(0).toUpperCase()}
                                </div>
                              )}
                              <div>
                                <div className="fw-semibold text-dark">{row.name}</div>
                                <small className="text-muted font-monospace">{row.admission_number}</small>
                              </div>
                            </div>
                          </td>
                          <td>
                            <span
                              className={`badge ${
                                row.attendance_percentage < (rosterPayload.shortage_threshold || 75)
                                  ? 'bg-danger-subtle text-danger'
                                  : 'bg-success-subtle text-success'
                              }`}
                            >
                              {row.attendance_percentage}%
                            </span>
                          </td>
                          <td>
                            <div className="btn-group btn-group-sm w-100" role="group">
                              {availableStatuses.map((stKey) => {
                                const meta = STATUS_META[stKey];
                                const active = row.status === stKey;
                                return (
                                  <button
                                    key={stKey}
                                    type="button"
                                    disabled={!canEdit}
                                    className={`btn ${active ? meta.btnClass : meta.outlineClass}`}
                                    onClick={() => updateStudentField(row.student_id, 'status', stKey)}
                                  >
                                    {meta.label}
                                  </button>
                                );
                              })}
                            </div>
                          </td>
                          <td>
                            <input
                              type="text"
                              className="form-control form-control-sm"
                              value={row.remarks || ''}
                              disabled={!canEdit}
                              placeholder="Optional note..."
                              maxLength={200}
                              onChange={(e) => updateStudentField(row.student_id, 'remarks', e.target.value)}
                            />
                          </td>
                          <td className="text-end pe-3">
                            {row.pending_correction ? (
                              <span className="badge bg-warning-subtle text-warning border border-warning-subtle">
                                Correction Pending
                              </span>
                            ) : isLocked && row.attendance_id && canCorrect ? (
                              <button
                                type="button"
                                className="btn btn-outline-warning btn-sm"
                                onClick={() => {
                                  setCorrectionRow(row);
                                  setCorrectionStatus(row.status);
                                  setCorrectionReason('');
                                }}
                              >
                                <i className="ti ti-edit me-1" /> Correct
                              </button>
                            ) : isLocked ? (
                              <span className="badge bg-light text-muted border">Locked</span>
                            ) : (
                              <span className="text-muted small">Editable</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {canEdit && rows.length > 0 && (
              <div className="card-footer bg-white border-top py-3 d-flex flex-wrap justify-content-between align-items-center gap-2">
                <small className="text-muted">
                  Save Draft keeps records editable. Submit Attendance locks this session; changes afterward require Correction Requests.
                </small>
                <div className="d-flex gap-2">
                  <button
                    type="button"
                    className="btn btn-outline-primary shadow-sm"
                    disabled={saving}
                    onClick={handleSaveDraft}
                  >
                    <i className="ti ti-device-floppy me-1" /> Save Draft
                  </button>
                  <button
                    type="button"
                    className="btn btn-primary shadow-sm"
                    disabled={saving}
                    onClick={() => setShowSubmitConfirm(true)}
                  >
                    <i className="ti ti-lock-check me-1" /> Submit Attendance
                  </button>
                </div>
              </div>
            )}
          </div>
        </>
      )}

      {/* Submit Confirmation Modal */}
      {showSubmitConfirm && (
        <div className="modal d-block" style={{ backgroundColor: 'rgba(0,0,0,0.45)' }} tabIndex="-1">
          <div className="modal-dialog modal-dialog-centered">
            <div className="modal-content border-0 shadow">
              <div className="modal-header">
                <h5 className="modal-title fw-bold">Confirm Attendance Submission</h5>
                <button type="button" className="btn-close" onClick={() => setShowSubmitConfirm(false)} />
              </div>
              <div className="modal-body">
                <p className="mb-2">
                  You are about to submit and lock attendance for <strong>{selectedClassObj?.name} - {selectedSectionName}</strong> on <strong>{selectedDate}</strong>.
                </p>
                <div className="bg-light p-3 rounded border small">
                  Present: <strong>{liveCounts.present}</strong> &bull; Absent: <strong>{liveCounts.absent}</strong> &bull; Half Day: <strong>{liveCounts.half_day}</strong> &bull; Leave: <strong>{liveCounts.leave}</strong>
                  {rosterPayload?.allow_late_status && <> &bull; Late: <strong>{liveCounts.late}</strong></>}
                </div>
                <p className="text-muted small mt-2 mb-0">
                  Once submitted, direct edits are locked and any changes will require an approved Correction Request.
                </p>
              </div>
              <div className="modal-footer">
                <button type="button" className="btn btn-light" onClick={() => setShowSubmitConfirm(false)} disabled={saving}>
                  Cancel
                </button>
                <button type="button" className="btn btn-primary" onClick={handleConfirmSubmit} disabled={saving}>
                  {saving ? 'Submitting...' : 'Confirm & Lock'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Correction Request Modal */}
      {correctionRow && (
        <div className="modal d-block" style={{ backgroundColor: 'rgba(0,0,0,0.45)' }} tabIndex="-1">
          <div className="modal-dialog modal-dialog-centered">
            <div className="modal-content border-0 shadow">
              <div className="modal-header">
                <h5 className="modal-title fw-bold">Request Attendance Correction</h5>
                <button type="button" className="btn-close" onClick={() => setCorrectionRow(null)} />
              </div>
              <form onSubmit={handleSubmitCorrection}>
                <div className="modal-body">
                  <p className="small text-muted mb-3">
                    Student: <strong>{correctionRow.name}</strong> ({correctionRow.admission_number}) &bull; Current: <strong>{correctionRow.status}</strong>
                  </p>
                  <div className="mb-3">
                    <label className="form-label small fw-semibold">Corrected Status</label>
                    <select
                      className="form-select"
                      value={correctionStatus}
                      onChange={(e) => setCorrectionStatus(e.target.value)}
                    >
                      {availableStatuses.map((st) => (
                        <option key={st} value={st}>
                          {STATUS_META[st].label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="mb-0">
                    <label className="form-label small fw-semibold">Reason <span className="text-danger">*</span></label>
                    <textarea
                      className="form-control"
                      rows={3}
                      required
                      placeholder="Explain why this submitted record needs correction..."
                      value={correctionReason}
                      onChange={(e) => setCorrectionReason(e.target.value)}
                    />
                  </div>
                </div>
                <div className="modal-footer">
                  <button type="button" className="btn btn-light" onClick={() => setCorrectionRow(null)} disabled={submittingCorrection}>
                    Cancel
                  </button>
                  <button type="submit" className="btn btn-warning" disabled={submittingCorrection}>
                    {submittingCorrection ? 'Submitting...' : 'Submit Request'}
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
