"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type {
  EvaluationCase,
  EvaluationCaseInput,
  EvaluationCaseUpdate,
  EvaluationStatus,
} from "@/lib/types";
import { EVALUATION_DATASET_ID } from "@/lib/types";

interface EvaluationCasesProps {
  cases: EvaluationCase[];
  selectedCaseIds: string[];
  isLoading: boolean;
  error: string | null;
  onReload: () => Promise<void>;
  onSelectionChange: (ids: string[]) => void;
  onCreate: (value: EvaluationCaseInput) => Promise<void>;
  onUpdate: (id: string, value: EvaluationCaseUpdate) => Promise<void>;
  onImport: (values: EvaluationCaseInput[]) => Promise<void>;
}

interface ImportError {
  line: number;
  messages: string[];
}

const STATUS_OPTIONS: Array<{
  value: EvaluationStatus;
  label: string;
}> = [
  { value: "draft_ready", label: "申請ドラフト作成" },
  { value: "needs_clarification", label: "追加確認が必要" },
  { value: "policy_blocked", label: "ポリシーで停止" },
  { value: "error", label: "エラー" },
];

const EMPTY_CASE: EvaluationCaseInput = {
  id: "",
  dataset_id: EVALUATION_DATASET_ID,
  title: "",
  input: "",
  expected_status: "draft_ready",
  expected_request: {
    departure: "",
    destination: "",
    schedule: "",
    purpose: "",
  },
  expected_policy_compliant: null,
  expected_clarification_fields: [],
  tags: [],
  enabled: true,
};

function caseToInput(value: EvaluationCase): EvaluationCaseInput {
  return {
    id: value.id,
    dataset_id: EVALUATION_DATASET_ID,
    title: value.title,
    input: value.input,
    expected_status: value.expected_status,
    expected_request: { ...value.expected_request },
    expected_policy_compliant: value.expected_policy_compliant,
    expected_clarification_fields: [...value.expected_clarification_fields],
    tags: [...value.tags],
    enabled: value.enabled,
  };
}

function toUpdate(value: EvaluationCaseInput): EvaluationCaseUpdate {
  return {
    dataset_id: EVALUATION_DATASET_ID,
    title: value.title,
    input: value.input,
    expected_status: value.expected_status,
    expected_request: value.expected_request,
    expected_policy_compliant: value.expected_policy_compliant,
    expected_clarification_fields: value.expected_clarification_fields,
    tags: value.tags,
    enabled: value.enabled,
  };
}

function splitValues(value: string): string[] {
  return Array.from(
    new Set(
      value
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function readString(
  value: Record<string, unknown>,
  key: string,
  messages: string[],
  required = false,
): string {
  const candidate = value[key];
  if (typeof candidate === "string" && (!required || candidate.trim())) {
    return candidate;
  }
  if (required) messages.push(`${key} は必須の文字列です`);
  else if (candidate !== undefined) messages.push(`${key} は文字列で指定してください`);
  return "";
}

function parseStringArray(
  value: unknown,
  key: string,
  messages: string[],
): string[] {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    messages.push(`${key} は文字列の配列で指定してください`);
    return [];
  }
  return Array.from(
    new Set(value.map((item) => String(item).trim()).filter(Boolean)),
  );
}

function validateImportedCase(
  value: unknown,
): { value?: EvaluationCaseInput; messages: string[] } {
  const messages: string[] = [];
  if (!isRecord(value)) {
    return { messages: ["JSON オブジェクトではありません"] };
  }

  const id = readString(value, "id", messages, true);
  if (id && !/^[a-z0-9][a-z0-9-]{2,79}$/.test(id)) {
    messages.push("id は英小文字・数字・ハイフンの 3〜80 文字で指定してください");
  }
  const title = readString(value, "title", messages, true);
  const input = readString(value, "input", messages, true);
  const suppliedDatasetId = value.dataset_id;
  if (
    suppliedDatasetId !== undefined &&
    suppliedDatasetId !== EVALUATION_DATASET_ID
  ) {
    const displayValue =
      typeof suppliedDatasetId === "string"
        ? `"${suppliedDatasetId}"`
        : JSON.stringify(suppliedDatasetId);
    messages.push(
      `dataset_id はこのリリースでは "${EVALUATION_DATASET_ID}" のみ指定できます。${displayValue} は使用できません`,
    );
  }
  const expectedStatus = value.expected_status;
  const statuses = STATUS_OPTIONS.map((option) => option.value);
  if (
    typeof expectedStatus !== "string" ||
    !statuses.includes(expectedStatus as EvaluationStatus)
  ) {
    messages.push(
      `expected_status は ${statuses.join(", ")} のいずれかです`,
    );
  }

  const requestValue = value.expected_request;
  if (requestValue !== undefined && !isRecord(requestValue)) {
    messages.push("expected_request は JSON オブジェクトで指定してください");
  }
  const request = isRecord(requestValue) ? requestValue : {};
  const expectedRequest = {
    departure: readString(request, "departure", messages),
    destination: readString(request, "destination", messages),
    schedule: readString(request, "schedule", messages),
    purpose: readString(request, "purpose", messages),
  };

  const compliant = value.expected_policy_compliant;
  if (
    compliant !== undefined &&
    compliant !== null &&
    typeof compliant !== "boolean"
  ) {
    messages.push("expected_policy_compliant は true、false、null のいずれかです");
  }
  if (value.enabled !== undefined && typeof value.enabled !== "boolean") {
    messages.push("enabled は true または false で指定してください");
  }

  const tags = parseStringArray(value.tags, "tags", messages);
  const clarificationFields = parseStringArray(
    value.expected_clarification_fields,
    "expected_clarification_fields",
    messages,
  );

  if (messages.length > 0) return { messages };
  return {
    messages,
    value: {
      id,
      dataset_id: EVALUATION_DATASET_ID,
      title,
      input,
      expected_status: expectedStatus as EvaluationStatus,
      expected_request: expectedRequest,
      expected_policy_compliant:
        compliant === undefined ? null : (compliant as boolean | null),
      expected_clarification_fields: clarificationFields,
      tags,
      enabled: value.enabled === undefined ? true : (value.enabled as boolean),
    },
  };
}

export function EvaluationCases({
  cases,
  selectedCaseIds,
  isLoading,
  error,
  onReload,
  onSelectionChange,
  onCreate,
  onUpdate,
  onImport,
}: EvaluationCasesProps) {
  const [query, setQuery] = useState("");
  const [tag, setTag] = useState("");
  const [editing, setEditing] = useState<EvaluationCaseInput | null>(null);
  const [editingOriginalId, setEditingOriginalId] = useState<string | null>(
    null,
  );
  const [editingVersion, setEditingVersion] = useState<number | undefined>();
  const [formError, setFormError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [busyCaseId, setBusyCaseId] = useState<string | null>(null);
  const [importCases, setImportCases] = useState<EvaluationCaseInput[]>([]);
  const [importErrors, setImportErrors] = useState<ImportError[]>([]);
  const [importFileName, setImportFileName] = useState("");
  const [isImporting, setIsImporting] = useState(false);
  const importInputRef = useRef<HTMLInputElement>(null);

  const tags = useMemo(
    () =>
      Array.from(new Set(cases.flatMap((item) => item.tags))).sort((a, b) =>
        a.localeCompare(b, "ja"),
      ),
    [cases],
  );

  const filteredCases = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase("ja");
    return cases.filter((item) => {
      const matchesQuery =
        !normalizedQuery ||
        [item.id, item.title, item.input, ...item.tags]
          .join(" ")
          .toLocaleLowerCase("ja")
          .includes(normalizedQuery);
      return matchesQuery && (!tag || item.tags.includes(tag));
    });
  }, [cases, query, tag]);

  const filteredEnabledIds = filteredCases
    .filter((item) => item.enabled)
    .map((item) => item.id);
  const allFilteredSelected =
    filteredEnabledIds.length > 0 &&
    filteredEnabledIds.every((id) => selectedCaseIds.includes(id));

  useEffect(() => {
    if (!editing) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !isSaving) setEditing(null);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [editing, isSaving]);

  const openNew = () => {
    setEditing({ ...EMPTY_CASE, expected_request: { ...EMPTY_CASE.expected_request } });
    setEditingOriginalId(null);
    setEditingVersion(undefined);
    setFormError(null);
  };

  const openEdit = (item: EvaluationCase) => {
    setEditing(caseToInput(item));
    setEditingOriginalId(item.id);
    setEditingVersion(item.version);
    setFormError(null);
  };

  const openDuplicate = (item: EvaluationCase) => {
    const suffix = `-copy-${Date.now().toString().slice(-6)}`;
    const id = `${item.id.slice(0, 80 - suffix.length)}${suffix}`;
    setEditing({
      ...caseToInput(item),
      id,
      title: `${item.title}（複製）`,
    });
    setEditingOriginalId(null);
    setEditingVersion(undefined);
    setFormError(null);
  };

  const saveCase = async () => {
    if (!editing) return;
    if (!/^[a-z0-9][a-z0-9-]{2,79}$/.test(editing.id)) {
      setFormError("ID は英小文字・数字・ハイフンの 3〜80 文字で指定してください。");
      return;
    }
    if (!editing.title.trim() || !editing.input.trim()) {
      setFormError("タイトルと入力文は必須です。");
      return;
    }
    setIsSaving(true);
    setFormError(null);
    try {
      if (editingOriginalId) {
        await onUpdate(editingOriginalId, {
          ...toUpdate(editing),
          version: editingVersion,
        });
      } else {
        await onCreate(editing);
      }
      setEditing(null);
    } catch (saveError) {
      setFormError(
        saveError instanceof Error ? saveError.message : "保存に失敗しました。",
      );
    } finally {
      setIsSaving(false);
    }
  };

  const toggleEnabled = async (item: EvaluationCase) => {
    setBusyCaseId(item.id);
    try {
      await onUpdate(item.id, {
        ...toUpdate(caseToInput(item)),
        enabled: !item.enabled,
        version: item.version,
      });
      if (item.enabled) {
        onSelectionChange(selectedCaseIds.filter((id) => id !== item.id));
      }
    } catch {
      // 親コンポーネントのエラー表示を使用する
    } finally {
      setBusyCaseId(null);
    }
  };

  const handleFile = async (file: File | undefined) => {
    setImportCases([]);
    setImportErrors([]);
    setImportFileName(file?.name ?? "");
    if (!file) return;
    try {
      const text = await file.text();
      const parsed: EvaluationCaseInput[] = [];
      const errors: ImportError[] = [];
      text.split(/\r?\n/).forEach((line, index) => {
        if (!line.trim()) return;
        try {
          const checked = validateImportedCase(JSON.parse(line));
          if (checked.value) parsed.push(checked.value);
          if (checked.messages.length > 0) {
            errors.push({ line: index + 1, messages: checked.messages });
          }
        } catch (parseError) {
          errors.push({
            line: index + 1,
            messages: [
              parseError instanceof Error
                ? `JSON の構文が不正です: ${parseError.message}`
                : "JSON の構文が不正です",
            ],
          });
        }
      });
      if (parsed.length === 0 && errors.length === 0) {
        errors.push({ line: 1, messages: ["有効な JSONL 行がありません"] });
      }
      setImportCases(parsed);
      setImportErrors(errors);
    } catch (fileError) {
      setImportErrors([
        {
          line: 0,
          messages: [
            fileError instanceof Error
              ? fileError.message
              : "ファイルを読み込めませんでした",
          ],
        },
      ]);
    }
  };

  const submitImport = async () => {
    if (importCases.length === 0 || importErrors.length > 0) return;
    setIsImporting(true);
    try {
      await onImport(importCases);
      setImportCases([]);
      setImportFileName("");
      if (importInputRef.current) importInputRef.current.value = "";
    } catch {
      // 親コンポーネントのエラー表示を使用する
    } finally {
      setIsImporting(false);
    }
  };

  return (
    <section aria-labelledby="cases-heading" className="eval-section">
      <div className="eval-section-heading">
        <div>
          <p className="eval-eyebrow">テストデータ管理</p>
          <h2 id="cases-heading">テストケース</h2>
          <p>
            有効なケースを選択すると、そのまま評価ランの対象にできます。
          </p>
        </div>
        <div className="eval-actions">
          <button className="eval-button eval-button-secondary" onClick={onReload}>
            更新
          </button>
          <button className="eval-button eval-button-primary" onClick={openNew}>
            ケースを追加
          </button>
        </div>
      </div>

      <div className="eval-toolbar" role="search">
        <label className="eval-field eval-field-grow">
          <span>検索</span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="ID、タイトル、入力文、タグ"
          />
        </label>
        <label className="eval-field">
          <span>タグ</span>
          <select value={tag} onChange={(event) => setTag(event.target.value)}>
            <option value="">すべて</option>
            {tags.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <div className="eval-selection-count" aria-live="polite">
          {selectedCaseIds.length} 件選択
        </div>
      </div>

      {error && <div className="eval-alert eval-alert-error">{error}</div>}
      {isLoading ? (
        <div className="eval-empty" aria-live="polite">
          読み込み中…
        </div>
      ) : (
        <div className="eval-table-wrap">
          <table className="eval-table">
            <thead>
              <tr>
                <th className="eval-check-cell">
                  <input
                    type="checkbox"
                    aria-label="表示中の有効なケースをすべて選択"
                    checked={allFilteredSelected}
                    onChange={() => {
                      if (allFilteredSelected) {
                        onSelectionChange(
                          selectedCaseIds.filter(
                            (id) => !filteredEnabledIds.includes(id),
                          ),
                        );
                      } else {
                        onSelectionChange(
                          Array.from(
                            new Set([...selectedCaseIds, ...filteredEnabledIds]),
                          ),
                        );
                      }
                    }}
                  />
                </th>
                <th>ケース</th>
                <th>期待結果</th>
                <th>タグ</th>
                <th>有効</th>
                <th aria-label="操作" />
              </tr>
            </thead>
            <tbody>
              {filteredCases.map((item) => (
                <tr key={item.id} className={!item.enabled ? "is-disabled" : ""}>
                  <td className="eval-check-cell">
                    <input
                      type="checkbox"
                      aria-label={`${item.title}を選択`}
                      disabled={!item.enabled}
                      checked={selectedCaseIds.includes(item.id)}
                      onChange={(event) =>
                        onSelectionChange(
                          event.target.checked
                            ? [...selectedCaseIds, item.id]
                            : selectedCaseIds.filter((id) => id !== item.id),
                        )
                      }
                    />
                  </td>
                  <td>
                    <strong>{item.title}</strong>
                    <span className="eval-code">{item.id}</span>
                    <p className="eval-clamp">{item.input}</p>
                  </td>
                  <td>
                    {
                      STATUS_OPTIONS.find(
                        (option) => option.value === item.expected_status,
                      )?.label
                    }
                  </td>
                  <td>
                    <div className="eval-tags">
                      {item.tags.length > 0
                        ? item.tags.map((itemTag) => (
                            <span key={itemTag}>{itemTag}</span>
                          ))
                        : "—"}
                    </div>
                  </td>
                  <td>
                    <button
                      type="button"
                      role="switch"
                      aria-checked={item.enabled}
                      aria-label={`${item.title}を${item.enabled ? "無効" : "有効"}にする`}
                      disabled={busyCaseId === item.id}
                      className={`eval-switch ${item.enabled ? "is-on" : ""}`}
                      onClick={() => void toggleEnabled(item)}
                    >
                      <span />
                    </button>
                  </td>
                  <td>
                    <div className="eval-row-actions">
                      <button onClick={() => openEdit(item)}>編集</button>
                      <button onClick={() => openDuplicate(item)}>複製</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {filteredCases.length === 0 && (
            <div className="eval-empty">条件に一致するケースはありません。</div>
          )}
        </div>
      )}

      <details className="eval-import">
        <summary>JSONL ファイルからインポート</summary>
        <div className="eval-import-body">
          <p>
            1 行に 1 ケースの JSON オブジェクトを記述してください。dataset_id
            は省略するか、固定値「{EVALUATION_DATASET_ID}」を指定してください。送信前にブラウザーで検証します。
          </p>
          <input
            ref={importInputRef}
            type="file"
            accept=".jsonl,.ndjson,application/json,text/plain"
            onChange={(event) => void handleFile(event.target.files?.[0])}
          />
          {importFileName && (
            <div className="eval-import-feedback" aria-live="polite">
              <strong>{importFileName}</strong>
              {importErrors.length > 0 ? (
                <>
                  <p className="eval-error-text">
                    {importErrors.length} 行に問題があります。API にはまだ送信されていません。
                  </p>
                  <ul>
                    {importErrors.map((item) => (
                      <li key={`${item.line}-${item.messages.join("-")}`}>
                        {item.line > 0 ? `${item.line} 行目: ` : ""}
                        {item.messages.join("／")}
                      </li>
                    ))}
                  </ul>
                </>
              ) : (
                <p className="eval-success-text">
                  {importCases.length} 件を検証しました。インポートできます。
                </p>
              )}
            </div>
          )}
          <button
            className="eval-button eval-button-primary"
            disabled={
              importCases.length === 0 ||
              importErrors.length > 0 ||
              isImporting
            }
            onClick={() => void submitImport()}
          >
            {isImporting ? "インポート中…" : `${importCases.length} 件をインポート`}
          </button>
        </div>
      </details>

      {editing && (
        <div
          className="eval-modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.currentTarget === event.target && !isSaving) {
              setEditing(null);
            }
          }}
        >
          <section
            className="eval-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="case-form-title"
          >
            <div className="eval-modal-heading">
              <div>
                <p className="eval-eyebrow">
                  {editingOriginalId ? "ケース編集" : "ケース作成"}
                </p>
                <h2 id="case-form-title">{editing.title || "新しいテストケース"}</h2>
              </div>
              <button
                className="eval-icon-button"
                aria-label="閉じる"
                onClick={() => setEditing(null)}
                disabled={isSaving}
              >
                ×
              </button>
            </div>
            <div className="eval-form-grid">
              <label className="eval-field">
                <span>ID</span>
                <input
                  value={editing.id}
                  disabled={Boolean(editingOriginalId)}
                  onChange={(event) =>
                    setEditing({ ...editing, id: event.target.value })
                  }
                  pattern="[a-z0-9][a-z0-9-]{2,79}"
                />
              </label>
              <div
                className="eval-dataset-lock"
                aria-label={`対象データセット ${EVALUATION_DATASET_ID}、このリリースでは固定`}
              >
                <span>対象データセット</span>
                <strong>{EVALUATION_DATASET_ID}</strong>
                <small>このリリースでは固定です</small>
              </div>
              <label className="eval-field eval-field-full">
                <span>タイトル</span>
                <input
                  value={editing.title}
                  onChange={(event) =>
                    setEditing({ ...editing, title: event.target.value })
                  }
                />
              </label>
              <label className="eval-field eval-field-full">
                <span>エージェントへの入力文</span>
                <textarea
                  rows={5}
                  value={editing.input}
                  onChange={(event) =>
                    setEditing({ ...editing, input: event.target.value })
                  }
                />
              </label>
              <label className="eval-field">
                <span>期待ステータス</span>
                <select
                  value={editing.expected_status}
                  onChange={(event) =>
                    setEditing({
                      ...editing,
                      expected_status: event.target.value as EvaluationStatus,
                    })
                  }
                >
                  {STATUS_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="eval-field">
                <span>期待するポリシー判定</span>
                <select
                  value={
                    editing.expected_policy_compliant === null
                      ? ""
                      : String(editing.expected_policy_compliant)
                  }
                  onChange={(event) =>
                    setEditing({
                      ...editing,
                      expected_policy_compliant:
                        event.target.value === ""
                          ? null
                          : event.target.value === "true",
                    })
                  }
                >
                  <option value="">指定なし</option>
                  <option value="true">準拠</option>
                  <option value="false">非準拠</option>
                </select>
              </label>
              {(
                [
                  ["departure", "出発地"],
                  ["destination", "目的地"],
                  ["schedule", "日程"],
                  ["purpose", "目的"],
                ] as const
              ).map(([key, label]) => (
                <label className="eval-field" key={key}>
                  <span>期待する{label}</span>
                  <input
                    value={editing.expected_request[key]}
                    onChange={(event) =>
                      setEditing({
                        ...editing,
                        expected_request: {
                          ...editing.expected_request,
                          [key]: event.target.value,
                        },
                      })
                    }
                  />
                </label>
              ))}
              <label className="eval-field eval-field-full">
                <span>追加確認フィールド（カンマ区切り）</span>
                <input
                  value={editing.expected_clarification_fields.join(", ")}
                  onChange={(event) =>
                    setEditing({
                      ...editing,
                      expected_clarification_fields: splitValues(
                        event.target.value,
                      ),
                    })
                  }
                />
              </label>
              <label className="eval-field eval-field-full">
                <span>タグ（カンマ区切り）</span>
                <input
                  value={editing.tags.join(", ")}
                  onChange={(event) =>
                    setEditing({
                      ...editing,
                      tags: splitValues(event.target.value),
                    })
                  }
                />
              </label>
              <label className="eval-checkbox-field eval-field-full">
                <input
                  type="checkbox"
                  checked={editing.enabled}
                  onChange={(event) =>
                    setEditing({ ...editing, enabled: event.target.checked })
                  }
                />
                このケースを有効にする
              </label>
            </div>
            {formError && (
              <div className="eval-alert eval-alert-error">{formError}</div>
            )}
            <div className="eval-modal-actions">
              <button
                className="eval-button eval-button-secondary"
                onClick={() => setEditing(null)}
                disabled={isSaving}
              >
                キャンセル
              </button>
              <button
                className="eval-button eval-button-primary"
                onClick={() => void saveCase()}
                disabled={isSaving}
              >
                {isSaving ? "保存中…" : "保存"}
              </button>
            </div>
          </section>
        </div>
      )}
    </section>
  );
}
