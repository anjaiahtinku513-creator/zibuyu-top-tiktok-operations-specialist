'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import type { DragEvent } from 'react';
import Image from 'next/image';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  FileText,
  ImagePlus,
  Link2,
  Plus,
  RefreshCw,
  Trash2,
  UploadCloud,
  UserRound,
  X,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Spinner } from '@/components/ui/spinner';
import { Textarea } from '@/components/ui/textarea';
import {
  ClassificationConfidence,
  ClassificationDetail,
  ClassificationResult,
  ClassificationStatus,
  MODELS,
  RunStatus,
} from '@/lib/zibuyu';

const MAX_PRODUCT_IMAGES = 60;
const MAX_VARIANTS = 24;
const IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp']);

type Variant = {
  id: string;
  name: string;
  colorNameZh: string;
  confidence: ClassificationConfidence | null;
  classificationReason: string;
  autoGrouped: boolean;
  reviewRequired: boolean;
  files: File[];
};

type ModelMode = 'preset' | 'custom';
type ClassifierPhase = 'idle' | 'uploading' | ClassificationStatus['state'];

type NewTaskFormProps = {
  token: string | null;
  codexAvailable: boolean;
  onCreated: (
    runId: string,
    status: RunStatus,
    runs?: Array<{ runId: string; status: RunStatus }>,
  ) => void;
};

function fileIdentity(file: File) {
  return `${file.name}\u0000${file.size}\u0000${file.lastModified}`;
}

function isImageFile(file: File) {
  return IMAGE_TYPES.has(file.type) || /\.(?:png|jpe?g|webp)$/i.test(file.name);
}

function newVariant(name = ''): Variant {
  return {
    id:
      typeof crypto !== 'undefined'
        ? crypto.randomUUID()
        : `variant-${Date.now()}`,
    name,
    colorNameZh: '',
    confidence: null,
    classificationReason: '',
    autoGrouped: false,
    reviewRequired: false,
    files: [],
  };
}

async function readApiResponse<T>(response: Response): Promise<T> {
  const payload = (await response.json().catch(() => null)) as
    | (T & { error?: string; detail?: string })
    | null;
  if (!response.ok) {
    throw new Error(
      payload?.error || payload?.detail || `本地服务返回 ${response.status}`,
    );
  }
  if (!payload) throw new Error('本地服务未返回有效结果');
  return payload;
}

function wait(milliseconds: number) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

export function NewTaskForm({
  token,
  codexAvailable,
  onCreated,
}: NewTaskFormProps) {
  const [sku, setSku] = useState('');
  const [amazonUrls, setAmazonUrls] = useState({ US: '', DE: '' });
  const [notes, setNotes] = useState('');
  const [modelMode, setModelMode] = useState<ModelMode>('preset');
  const [selectedModels, setSelectedModels] = useState<string[]>(['德1']);
  const [customModelName, setCustomModelName] = useState('');
  const [customMarket, setCustomMarket] = useState<'US' | 'DE'>('DE');
  const [modelReference, setModelReference] = useState<File | null>(null);
  const [sourceFiles, setSourceFiles] = useState<File[]>([]);
  const [variants, setVariants] = useState<Variant[]>([]);
  const [classificationBatchId, setClassificationBatchId] = useState<
    string | null
  >(null);
  const [classificationStatus, setClassificationStatus] =
    useState<ClassificationStatus | null>(null);
  const [classifierPhase, setClassifierPhase] =
    useState<ClassifierPhase>('idle');
  const [classificationWarnings, setClassificationWarnings] = useState<
    string[]
  >([]);
  const [classifierError, setClassifierError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const classifierRequest = useRef(0);
  const submissionRequest = useRef<{ signature: string; id: string } | null>(
    null,
  );

  const selected = useMemo(
    () => MODELS.filter((model) => selectedModels.includes(model.id)),
    [selectedModels],
  );
  const selectedMarkets =
    modelMode === 'custom'
      ? [customMarket]
      : [...new Set(selected.map((model) => model.marketCode))];

  useEffect(
    () => () => {
      classifierRequest.current += 1;
    },
    [],
  );

  function applyClassification(result: ClassificationResult, files: File[]) {
    const filesById = new Map(
      files.map((file, index) => [
        `img-${String(index + 1).padStart(3, '0')}`,
        file,
      ]),
    );
    const grouped: Variant[] = result.groups.map((group) => ({
      id: group.id,
      name: group.colorName,
      colorNameZh: group.colorNameZh,
      confidence: group.confidence,
      classificationReason: group.reason,
      autoGrouped: true,
      reviewRequired: false,
      files: group.imageIds
        .map((imageId) => filesById.get(imageId))
        .filter((file): file is File => Boolean(file)),
    }));
    const pending: Variant[] = result.unassigned.map((item, index) => {
      const file = filesById.get(item.imageId);
      return {
        id: `pending-${String(index + 1).padStart(3, '0')}`,
        name: `待确认 ${index + 1}`,
        colorNameZh: '待确认',
        confidence: 'low',
        classificationReason: item.reason,
        autoGrouped: true,
        reviewRequired: true,
        files: file ? [file] : [],
      };
    });
    setVariants([...grouped, ...pending]);
    setClassificationWarnings(result.warnings);
  }

  async function classifyFiles(files: File[]) {
    const requestNumber = classifierRequest.current + 1;
    classifierRequest.current = requestNumber;
    setClassifierError('');
    setError('');
    setClassificationWarnings([]);
    setClassificationStatus(null);
    setClassificationBatchId(null);
    setVariants([]);

    if (!token) {
      setClassifierPhase('failed');
      setClassifierError('本地服务尚未连接，请刷新页面');
      return;
    }
    if (!codexAvailable) {
      setClassifierPhase('failed');
      setClassifierError('未检测到可用的本机 Codex CLI');
      return;
    }

    setClassifierPhase('uploading');
    try {
      const data = new FormData();
      data.append('payload', JSON.stringify({ sku: sku.trim() }));
      for (const file of files) data.append('image', file, file.name);

      const response = await fetch('/api/classifications', {
        method: 'POST',
        headers: { 'x-zibuyu-token': token },
        body: data,
      });
      const created = await readApiResponse<{
        batchId: string;
        status: ClassificationStatus;
      }>(response);
      if (classifierRequest.current !== requestNumber) return;

      setClassificationBatchId(created.batchId);
      setClassificationStatus(created.status);
      setClassifierPhase(created.status.state);

      const deadline = Date.now() + 20 * 60 * 1000;
      while (Date.now() < deadline) {
        await wait(1200);
        if (classifierRequest.current !== requestNumber) return;
        const detailResponse = await fetch(
          `/api/classifications/${encodeURIComponent(created.batchId)}`,
        );
        const detail =
          await readApiResponse<ClassificationDetail>(detailResponse);
        if (classifierRequest.current !== requestNumber) return;
        setClassificationStatus(detail.status);
        setClassifierPhase(detail.status.state);

        if (detail.status.state === 'completed') {
          if (!detail.result)
            throw new Error('颜色识别已结束，但没有可用的分组结果');
          applyClassification(detail.result, files);
          return;
        }
        if (detail.status.state === 'failed') {
          throw new Error(
            detail.status.error || detail.status.note || '颜色识别失败',
          );
        }
      }
      throw new Error('颜色识别等待超时，批次已保留，可重新识别');
    } catch (classificationError) {
      if (classifierRequest.current !== requestNumber) return;
      setClassifierPhase('failed');
      setClassifierError(
        classificationError instanceof Error
          ? classificationError.message
          : '颜色识别失败',
      );
    }
  }

  function acceptBulkFiles(incoming: File[]) {
    const valid = incoming.filter(isImageFile);
    if (!valid.length) {
      setClassifierError('请选择 PNG、JPEG 或 WebP 商品图');
      return;
    }

    const next = [...sourceFiles];
    const known = new Set(next.map(fileIdentity));
    for (const file of valid) {
      const identity = fileIdentity(file);
      if (!known.has(identity) && next.length < MAX_PRODUCT_IMAGES) {
        next.push(file);
        known.add(identity);
      }
    }
    if (next.length === sourceFiles.length) {
      setClassifierError(
        sourceFiles.length >= MAX_PRODUCT_IMAGES
          ? `单次最多识别 ${MAX_PRODUCT_IMAGES} 张商品图`
          : '没有新增商品图',
      );
      return;
    }
    setSourceFiles(next);
    void classifyFiles(next);
  }

  function clearBulkFiles() {
    classifierRequest.current += 1;
    setSourceFiles([]);
    setVariants([]);
    setClassificationBatchId(null);
    setClassificationStatus(null);
    setClassificationWarnings([]);
    setClassifierPhase('idle');
    setClassifierError('');
  }

  function addVariant() {
    if (variants.length >= MAX_VARIANTS) return;
    setVariants((current) => [...current, newVariant()]);
  }

  function updateVariant(id: string, patch: Partial<Variant>) {
    setVariants((current) =>
      current.map((variant) =>
        variant.id === id ? { ...variant, ...patch } : variant,
      ),
    );
  }

  function renameVariant(id: string, name: string) {
    setVariants((current) =>
      current.map((variant) =>
        variant.id === id
          ? {
              ...variant,
              name,
              reviewRequired:
                variant.reviewRequired &&
                (!name.trim() || /^待确认(?:\s|$)/.test(name.trim())),
            }
          : variant,
      ),
    );
  }

  function addFiles(id: string, incoming: File[]) {
    const imageFiles = incoming.filter(isImageFile);
    setVariants((current) =>
      current.map((variant) => {
        if (variant.id !== id) return variant;
        const next = [...variant.files];
        const known = new Set(next.map(fileIdentity));
        for (const file of imageFiles) {
          const identity = fileIdentity(file);
          if (!known.has(identity) && next.length < MAX_PRODUCT_IMAGES) {
            next.push(file);
            known.add(identity);
          }
        }
        return { ...variant, files: next };
      }),
    );
  }

  function removeVariant(id: string) {
    setVariants((current) => current.filter((variant) => variant.id !== id));
  }

  async function submit() {
    setError('');
    if (!token) return setError('本地服务尚未连接，请刷新页面');
    if (!codexAvailable) return setError('未检测到可用的本机 Codex CLI');
    if (['uploading', 'queued', 'running'].includes(classifierPhase)) {
      return setError('请等待颜色自动归类完成');
    }
    if (!sku.trim()) return setError('请填写货号');
    if (modelMode === 'preset' && !selectedModels.length)
      return setError('请至少选择一位固定模特');
    if (!variants.length) return setError('请先上传商品图并完成颜色归类');
    if (variants.length > MAX_VARIANTS) {
      return setError(`单个制作单最多支持 ${MAX_VARIANTS} 个颜色，请拆分任务`);
    }
    if (variants.some((variant) => !variant.name.trim()))
      return setError('请填写所有颜色名称');
    if (variants.some((variant) => variant.reviewRequired)) {
      return setError('仍有待确认图片，请先填写其真实颜色');
    }
    if (variants.some((variant) => !variant.files.length)) {
      return setError('每个颜色至少需要一张商品图');
    }
    const allFiles = variants.flatMap((variant) => variant.files);
    if (allFiles.length > MAX_PRODUCT_IMAGES) {
      return setError(`单个制作单最多上传 ${MAX_PRODUCT_IMAGES} 张商品图`);
    }
    const fileKeys = allFiles.map(fileIdentity);
    if (new Set(fileKeys).size !== fileKeys.length) {
      return setError('同一张商品图不能同时归入多个颜色');
    }
    const names = variants.map((variant) =>
      variant.name.trim().toLocaleLowerCase(),
    );
    if (new Set(names).size !== names.length)
      return setError('颜色名称不能重复');
    if (modelMode === 'custom' && !customModelName.trim()) {
      return setError('请填写 PopBoom 自定义模特名称');
    }
    if (modelMode === 'custom' && !modelReference) {
      return setError('请上传自定义模特的身份参考图');
    }

    const data = new FormData();
    data.append(
      'payload',
      JSON.stringify({
        sku: sku.trim(),
        amazonUrl:
          modelMode === 'custom' ? amazonUrls[customMarket].trim() : '',
        amazonUrls,
        notes: notes.trim(),
        modelMode,
        ...(modelMode === 'preset' ? { modelPresets: selectedModels } : {}),
        customModelName: customModelName.trim(),
        customMarket,
        classificationBatchId,
        variants: variants.map((variant) => ({
          id: variant.id,
          name: variant.name.trim(),
          colorNameZh: variant.colorNameZh,
          confidence: variant.confidence,
          classificationReason: variant.classificationReason,
          autoGrouped: variant.autoGrouped,
        })),
      }),
    );
    if (modelMode === 'custom' && modelReference) {
      data.append('model__reference', modelReference, modelReference.name);
    }
    for (const variant of variants) {
      for (const file of variant.files)
        data.append(`image__${variant.id}`, file, file.name);
    }

    setSubmitting(true);
    try {
      const signature = JSON.stringify([
        data.get('payload'),
        allFiles.map(fileIdentity),
        modelReference ? fileIdentity(modelReference) : null,
      ]);
      if (submissionRequest.current?.signature !== signature)
        submissionRequest.current = { signature, id: crypto.randomUUID() };
      const response = await fetch('/api/runs', {
        method: 'POST',
        headers: {
          'x-zibuyu-token': token,
          'x-zibuyu-request-id': submissionRequest.current.id,
        },
        body: data,
      });
      const result = await readApiResponse<{
        runId: string;
        status: RunStatus;
        runs?: Array<{ runId: string; status: RunStatus }>;
      }>(response);
      onCreated(result.runId, result.status, result.runs);
    } catch (submitError) {
      setError(
        submitError instanceof Error ? submitError.message : '制作任务创建失败',
      );
    } finally {
      setSubmitting(false);
    }
  }

  const summaryMarket =
    modelMode === 'preset'
      ? [...new Set(selected.map((model) => model.market))].join(' / ') ||
        '未选择'
      : customMarket === 'DE'
        ? '德国'
        : '美国';
  const summaryLocale =
    modelMode === 'preset'
      ? [...new Set(selected.map((model) => model.locale))].join(' / ') ||
        '未选择'
      : customMarket === 'DE'
        ? 'de-DE'
        : 'en-US';
  const summaryModel =
    modelMode === 'preset'
      ? selected.map((model) => model.id).join('、') || '未选择'
      : customModelName || '自定义模特';
  const pendingCount = variants.filter(
    (variant) => variant.reviewRequired,
  ).length;
  const classifiedImageCount = variants.reduce(
    (sum, variant) => sum + variant.files.length,
    0,
  );
  const classifierBusy = ['uploading', 'queued', 'running'].includes(
    classifierPhase,
  );

  return (
    <div className="grid min-w-0 gap-6 xl:grid-cols-[minmax(0,1fr)_300px]">
      <div className="min-w-0">
        <div className="mb-5 flex flex-wrap items-end justify-between gap-3 border-b border-border pb-5">
          <div>
            <p className="mb-1 text-sm font-medium text-[#0071e3]">
              从一件好衣服开始
            </p>
            <h1 className="text-[30px] font-semibold tracking-tight">
              新建制作单
            </h1>
          </div>
          <Badge
            variant="outline"
            className="h-7 rounded-md border-[#c6dcf8] bg-[#edf5ff] px-2.5 text-[#0056ad]"
          >
            生成前由你确认
          </Badge>
        </div>

        <div className="surface-card overflow-hidden">
          <section className="px-4 py-5 sm:px-5">
            <SectionTitle number="01" icon={<FileText />} title="商品信息" />
            <div className="grid gap-4 md:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="sku">货号</Label>
                <Input
                  id="sku"
                  value={sku}
                  onChange={(event) => setSku(event.target.value)}
                  placeholder="例如 M4D809-EU"
                  className="h-10"
                  maxLength={64}
                />
              </div>
              {selectedMarkets.map((market) => (
                <div key={market} className="space-y-2">
                  <Label htmlFor={`amazon-${market}`}>
                    {market === 'US' ? '美国' : '德国'} Amazon 商品链接
                  </Label>
                  <div className="relative">
                    <Link2 className="pointer-events-none absolute left-3 top-3 size-4 text-muted-foreground" />
                    <Input
                      id={`amazon-${market}`}
                      type="url"
                      value={amazonUrls[market]}
                      onChange={(event) =>
                        setAmazonUrls((current) => ({
                          ...current,
                          [market]: event.target.value,
                        }))
                      }
                      placeholder={
                        market === 'US'
                          ? 'https://www.amazon.com/...'
                          : 'https://www.amazon.de/...'
                      }
                      className="h-10 pl-9"
                    />
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="border-t border-border px-4 py-5 sm:px-5">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <SectionTitle
                number="02"
                icon={<ImagePlus />}
                title="颜色与商品图"
                compact
              />
              <Button
                variant="outline"
                size="sm"
                onClick={addVariant}
                disabled={variants.length >= MAX_VARIANTS}
              >
                <Plus />
                手动补颜色
              </Button>
            </div>

            <BulkImageUpload files={sourceFiles} onFiles={acceptBulkFiles} />

            {sourceFiles.length ? (
              <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-muted/45 px-3 py-2.5">
                <div className="flex min-w-0 items-center gap-2 text-sm">
                  {classifierBusy ? (
                    <Spinner />
                  ) : classifierPhase === 'completed' ? (
                    <CheckCircle2 className="size-4 text-emerald-700" />
                  ) : (
                    <AlertTriangle className="size-4 text-amber-600" />
                  )}
                  <span className="font-medium">
                    {classifierPhaseLabel(classifierPhase)}
                  </span>
                  <span className="truncate text-xs text-muted-foreground">
                    {classificationStatus?.note ||
                      `${sourceFiles.length} 张商品图`}
                  </span>
                </div>
                <div className="flex items-center gap-1">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => void classifyFiles(sourceFiles)}
                    disabled={classifierBusy}
                  >
                    <RefreshCw
                      className={classifierBusy ? 'animate-spin' : ''}
                    />
                    重新识别
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={clearBulkFiles}
                  >
                    <Trash2 />
                    清空
                  </Button>
                </div>
              </div>
            ) : null}

            {classifierError ? (
              <div
                role="alert"
                className="mt-3 border-l-2 border-amber-600 bg-amber-50 px-3 py-2 text-sm text-amber-800"
              >
                {classifierError}。图片已保留，可重新识别或手动补颜色。
              </div>
            ) : null}

            {classificationWarnings.length ? (
              <div className="mt-3 border-l-2 border-amber-500 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-800">
                {classificationWarnings.join('；')}
              </div>
            ) : null}

            {variants.length ? (
              <div className="mt-4 space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
                  <p className="font-medium">
                    {variants.length} 个颜色 · {classifiedImageCount} 张图
                  </p>
                  {pendingCount ? (
                    <Badge
                      variant="outline"
                      className="rounded-md border-amber-300 bg-amber-50 text-amber-800"
                    >
                      {pendingCount} 组待确认
                    </Badge>
                  ) : null}
                </div>
                {variants.map((variant, index) => (
                  <VariantUpload
                    key={variant.id}
                    variant={variant}
                    index={index}
                    onNameChange={(name) => renameVariant(variant.id, name)}
                    onFiles={(files) => addFiles(variant.id, files)}
                    onRemoveFile={(fileIndex) =>
                      updateVariant(variant.id, {
                        files: variant.files.filter(
                          (_, currentIndex) => currentIndex !== fileIndex,
                        ),
                      })
                    }
                    onRemove={() => removeVariant(variant.id)}
                  />
                ))}
              </div>
            ) : !classifierBusy ? (
              <p className="mt-4 text-sm text-muted-foreground">
                上传后会自动按服装颜色生成分组，也可以手动补充颜色。
              </p>
            ) : null}
          </section>

          <section className="border-t border-border px-4 py-5 sm:px-5">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <SectionTitle
                number="03"
                icon={<UserRound />}
                title={
                  modelMode === 'preset' ? '固定模特（可多选）' : '自定义模特'
                }
                compact
              />
              <div className="inline-grid h-9 grid-cols-2 rounded-md border border-border bg-muted/50 p-1 text-sm">
                {(['preset', 'custom'] as const).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => setModelMode(mode)}
                    className={`min-w-24 rounded-[4px] px-3 transition-colors ${
                      modelMode === mode
                        ? 'bg-white font-medium text-foreground shadow-sm'
                        : 'text-muted-foreground'
                    }`}
                  >
                    {mode === 'preset' ? '固定模特' : '自定义'}
                  </button>
                ))}
              </div>
            </div>

            {modelMode === 'preset' ? (
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                {MODELS.map((model) => {
                  const active = selectedModels.includes(model.id);
                  return (
                    <button
                      key={model.id}
                      type="button"
                      onClick={() =>
                        setSelectedModels((current) =>
                          active
                            ? current.filter((id) => id !== model.id)
                            : [...current, model.id],
                        )
                      }
                      className={`min-h-24 rounded-md border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                        active
                          ? 'border-[#0071e3] bg-[#edf5ff]'
                          : 'border-border bg-white hover:bg-muted/40'
                      }`}
                      aria-pressed={active}
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-base font-semibold">
                          {model.id}
                        </span>
                        {active ? (
                          <CheckCircle2 className="size-4 text-[#0071e3]" />
                        ) : null}
                      </div>
                      <p className="mt-2 text-xs text-muted-foreground">
                        {model.market} · {model.size} · {model.tone}
                      </p>
                    </button>
                  );
                })}
              </div>
            ) : (
              <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_180px]">
                <div className="space-y-4">
                  <div className="space-y-2">
                    <Label htmlFor="custom-model-name">
                      PopBoom 自定义模特名
                    </Label>
                    <Input
                      id="custom-model-name"
                      value={customModelName}
                      onChange={(event) =>
                        setCustomModelName(event.target.value)
                      }
                      maxLength={80}
                      placeholder="与 PopBoom 模特库一致"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>市场与口播语言</Label>
                    <div className="grid grid-cols-2 gap-2">
                      {(
                        [
                          ['DE', '德国 · de-DE'],
                          ['US', '美国 · en-US'],
                        ] as const
                      ).map(([code, label]) => (
                        <button
                          key={code}
                          type="button"
                          onClick={() => setCustomMarket(code)}
                          className={`h-10 rounded-md border px-3 text-sm ${
                            customMarket === code
                              ? 'border-[#0071e3] bg-[#edf5ff] font-medium'
                              : 'border-border bg-white text-muted-foreground'
                          }`}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
                <SingleImageUpload
                  file={modelReference}
                  onChange={setModelReference}
                />
              </div>
            )}
          </section>

          <section className="border-t border-border px-4 py-5 sm:px-5">
            <SectionTitle number="04" icon={<FileText />} title="补充要求" />
            <Label htmlFor="notes" className="sr-only">
              补充要求
            </Label>
            <Textarea
              id="notes"
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              className="mt-2 min-h-24 resize-y"
              maxLength={2000}
              placeholder="例如重点展示袖口、避免卧室场景、需要更强的显瘦证明"
            />
          </section>
        </div>

        {error ? (
          <div
            role="alert"
            className="mt-4 border-l-2 border-destructive bg-red-50 px-3 py-2 text-sm text-red-700"
          >
            {error}
          </div>
        ) : null}

        <div className="mt-6 flex items-center justify-between gap-4">
          <p className="max-w-xl text-xs leading-5 text-muted-foreground">
            此次提交会启动准备阶段；PopBoom 生成会在审核后单独授权。
          </p>
          <Button
            size="lg"
            className="shrink-0 bg-[#0071e3] px-4 hover:bg-[#005bb8]"
            onClick={submit}
            disabled={submitting || !token || classifierBusy}
          >
            {submitting ? <Spinner /> : null}
            {submitting
              ? '正在上传'
              : classifierBusy
                ? '正在识别颜色'
                : '开始准备'}
            {!submitting && !classifierBusy ? <ChevronRight /> : null}
          </Button>
        </div>
      </div>

      <aside className="surface-card h-fit overflow-hidden xl:sticky xl:top-0">
        <div className="border-b border-border bg-blue-50/60 px-4 py-5 text-slate-900">
          <p className="text-xs uppercase text-muted-foreground">制作概览</p>
          <h2 className="mt-1 text-sm font-semibold">本次制作</h2>
        </div>
        <dl className="divide-y divide-border px-4 text-sm">
          <SummaryRow label="市场" value={summaryMarket} />
          <SummaryRow label="语言" value={summaryLocale} />
          <SummaryRow label="模特" value={summaryModel} />
          <SummaryRow label="颜色" value={`${variants.length} 个`} />
          <SummaryRow
            label="预计视频"
            value={`${variants.length} 色 × ${modelMode === 'preset' ? selected.length : 1} 位模特 = ${variants.length * (modelMode === 'preset' ? selected.length : 1)} 条`}
          />
          <SummaryRow label="商品图" value={`${classifiedImageCount} 张`} />
          <SummaryRow label="时长" value="15 秒" />
          <SummaryRow label="画面" value="9:16 · 720p" />
        </dl>
        <div className="m-4 rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">
          <div className="flex items-center gap-2 font-medium">
            <CheckCircle2 className="size-4" />
            准备完成后再确认生成
          </div>
          <p className="mt-1.5 text-xs leading-5 text-emerald-700">
            每个颜色使用全部所选模特。准备完成后，按模特分别审核并确认生成。
          </p>
        </div>
      </aside>
    </div>
  );
}

function BulkImageUpload({
  files,
  onFiles,
}: {
  files: File[];
  onFiles: (files: File[]) => void;
}) {
  function drop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    onFiles(Array.from(event.dataTransfer.files));
  }

  return (
    <div>
      <div
        onDragOver={(event) => event.preventDefault()}
        onDrop={drop}
        className="min-h-40 rounded-md border border-dashed border-[#b6b8bd] bg-muted/25 transition-colors hover:border-[#79b4f3] hover:bg-[#f2f7ff]"
      >
        <label className="flex min-h-40 cursor-pointer flex-col items-center justify-center gap-2 px-5 text-center sm:flex-row sm:justify-between sm:text-left">
          <span className="grid size-11 shrink-0 place-items-center rounded-md bg-[#0071e3] text-white">
            <UploadCloud className="size-5" />
          </span>
          <span className="min-w-0 flex-1 sm:px-3">
            <span className="block text-sm font-semibold">
              {files.length ? '继续添加商品图' : '一次上传全部颜色商品图'}
            </span>
            <span className="mt-1 block text-xs leading-5 text-muted-foreground">
              PNG、JPEG、WebP · 最多 {MAX_PRODUCT_IMAGES} 张 ·
              上传后自动按服装颜色归类
            </span>
          </span>
          <span className="rounded-2xl border border-border bg-white px-3 py-2 text-xs font-semibold text-foreground shadow-sm">
            选择图片
          </span>
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp"
            multiple
            className="sr-only"
            onChange={(event) => {
              onFiles(Array.from(event.target.files ?? []));
              event.currentTarget.value = '';
            }}
          />
        </label>
      </div>
      {files.length ? (
        <div className="mt-3 flex gap-2 overflow-x-auto rounded-md border border-border bg-muted/25 p-2">
          {files.map((file) => (
            <ImageFilePreview key={fileIdentity(file)} file={file} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function VariantUpload({
  variant,
  index,
  onNameChange,
  onFiles,
  onRemoveFile,
  onRemove,
}: {
  variant: Variant;
  index: number;
  onNameChange: (name: string) => void;
  onFiles: (files: File[]) => void;
  onRemoveFile: (index: number) => void;
  onRemove: () => void;
}) {
  function drop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    onFiles(Array.from(event.dataTransfer.files));
  }

  return (
    <div
      className={`rounded-md border p-3.5 ${variant.reviewRequired ? 'border-amber-300 bg-amber-50/45' : 'border-border bg-muted/20'}`}
    >
      <div className="grid gap-3 sm:grid-cols-[190px_minmax(0,1fr)_32px]">
        <div className="min-w-0 space-y-2">
          <div className="flex items-center justify-between gap-2">
            <Label htmlFor={`variant-${variant.id}`}>颜色 {index + 1}</Label>
            {variant.confidence ? (
              <ConfidenceBadge
                confidence={variant.confidence}
                reviewRequired={variant.reviewRequired}
              />
            ) : null}
          </div>
          <div className="relative">
            <span
              className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 rounded-full border border-black/10 shadow-sm"
              style={{ backgroundColor: colorSwatch(variant.name) }}
              aria-hidden="true"
            />
            <Input
              id={`variant-${variant.id}`}
              value={variant.name}
              onChange={(event) => onNameChange(event.target.value)}
              className="h-10 bg-white pl-9"
              placeholder="填写颜色名"
              maxLength={50}
            />
          </div>
          {variant.colorNameZh || variant.classificationReason ? (
            <p
              className="line-clamp-2 text-xs leading-5 text-muted-foreground"
              title={variant.classificationReason}
            >
              {variant.colorNameZh ? `${variant.colorNameZh} · ` : ''}
              {variant.classificationReason}
            </p>
          ) : null}
        </div>
        <div
          onDragOver={(event) => event.preventDefault()}
          onDrop={drop}
          className="min-h-24 rounded-md border border-dashed border-[#b6b8bd] bg-white transition-colors hover:border-[#79b4f3] hover:bg-[#f2f7ff]"
        >
          <label className="flex min-h-24 cursor-pointer items-center justify-center gap-3 px-4 text-center">
            <UploadCloud className="size-5 shrink-0 text-[#0071e3]" />
            <span className="text-sm text-muted-foreground">
              {variant.files.length
                ? `已归入 ${variant.files.length} 张`
                : '补充商品图'}
            </span>
            <input
              type="file"
              accept="image/png,image/jpeg,image/webp"
              multiple
              className="sr-only"
              onChange={(event) => {
                onFiles(Array.from(event.target.files ?? []));
                event.currentTarget.value = '';
              }}
            />
          </label>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="mt-6 text-muted-foreground hover:text-destructive"
          onClick={onRemove}
          aria-label={`删除颜色 ${index + 1}`}
          title="删除颜色"
        >
          <Trash2 />
        </Button>
      </div>
      {variant.files.length ? (
        <div className="mt-3 flex gap-2 overflow-x-auto pb-1">
          {variant.files.map((file, fileIndex) => (
            <ImageFilePreview
              key={`${fileIdentity(file)}-${fileIndex}`}
              file={file}
              onRemove={() => onRemoveFile(fileIndex)}
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ConfidenceBadge({
  confidence,
  reviewRequired,
}: {
  confidence: ClassificationConfidence;
  reviewRequired: boolean;
}) {
  const label = reviewRequired
    ? '待确认'
    : confidence === 'high'
      ? '高置信'
      : confidence === 'medium'
        ? '中置信'
        : '低置信';
  return (
    <Badge
      variant="outline"
      className={`h-6 shrink-0 rounded-md px-1.5 text-xs ${
        confidence === 'high' && !reviewRequired
          ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
          : 'border-amber-300 bg-amber-50 text-amber-800'
      }`}
    >
      {label}
    </Badge>
  );
}

function classifierPhaseLabel(phase: ClassifierPhase) {
  return {
    idle: '等待商品图',
    uploading: '正在上传',
    queued: '等待颜色识别',
    running: '正在自动归类',
    completed: '自动归类完成',
    failed: '自动归类失败',
  }[phase];
}

function ImageFilePreview({
  file,
  onRemove,
}: {
  file: File;
  onRemove?: () => void;
}) {
  const url = useMemo(() => URL.createObjectURL(file), [file]);
  useEffect(() => () => URL.revokeObjectURL(url), [url]);

  return (
    <div
      className="group relative size-17 shrink-0 surface-card overflow-hidden"
      title={file.name}
    >
      <Image
        src={url}
        alt={file.name}
        fill
        sizes="64px"
        unoptimized
        className="object-cover"
      />
      {onRemove ? (
        <button
          type="button"
          onClick={onRemove}
          className="absolute right-1 top-1 grid size-5 place-items-center rounded-full bg-black/65 text-white opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100"
          aria-label={`移除 ${file.name}`}
          title="移除图片"
        >
          <X className="size-3" />
        </button>
      ) : null}
    </div>
  );
}

function SingleImageUpload({
  file,
  onChange,
}: {
  file: File | null;
  onChange: (file: File | null) => void;
}) {
  const url = useMemo(() => (file ? URL.createObjectURL(file) : ''), [file]);
  useEffect(() => {
    if (!url) return;
    return () => URL.revokeObjectURL(url);
  }, [url]);

  return (
    <div className="space-y-2">
      <Label>模特身份参考图</Label>
      <label className="relative flex aspect-square cursor-pointer items-center justify-center overflow-hidden rounded-md border border-dashed border-[#b6b8bd] bg-muted/25 transition-colors hover:border-[#79b4f3] hover:bg-[#f2f7ff]">
        {url ? (
          <Image
            src={url}
            alt="自定义模特参考"
            fill
            sizes="180px"
            unoptimized
            className="object-cover"
          />
        ) : (
          <span className="flex flex-col items-center gap-2 px-3 text-center text-xs text-muted-foreground">
            <UploadCloud className="size-5" />
            选择图片
          </span>
        )}
        <input
          type="file"
          accept="image/png,image/jpeg,image/webp"
          className="sr-only"
          onChange={(event) => onChange(event.target.files?.[0] ?? null)}
        />
      </label>
      {file ? (
        <Button
          variant="ghost"
          size="sm"
          className="w-full text-muted-foreground"
          onClick={() => onChange(null)}
        >
          <X />
          移除参考图
        </Button>
      ) : null}
    </div>
  );
}

function colorSwatch(name: string) {
  const normalized = name.trim().toLowerCase();
  const swatches: Array<[RegExp, string]> = [
    [/(black|黑)/, '#26272a'],
    [/(white|ivory|cream|off[- ]?white|白|象牙|奶油)/, '#f4f1e9'],
    [/(gray|grey|silver|灰|银)/, '#909298'],
    [/(navy|藏青|深蓝)/, '#29354b'],
    [/(blue|蓝)/, '#5b7fa5'],
    [/(burgundy|wine|maroon|red|酒红|枣红|红)/, '#0071e3'],
    [/(pink|rose|粉)/, '#d394a5'],
    [/(khaki|卡其)/, '#b19d7a'],
    [/(beige|apricot|杏|米色)/, '#d7c1a0'],
    [/(brown|coffee|camel|棕|咖|驼)/, '#795947'],
    [/(army|olive|green|军绿|橄榄|绿)/, '#68725e'],
    [/(yellow|黄)/, '#d4b24d'],
    [/(orange|橙)/, '#d77b42'],
    [/(purple|violet|紫)/, '#786287'],
  ];
  return (
    swatches.find(([pattern]) => pattern.test(normalized))?.[1] ?? '#c9cbd0'
  );
}

function SectionTitle({
  number,
  icon,
  title,
  compact = false,
}: {
  number: string;
  icon: React.ReactNode;
  title: string;
  compact?: boolean;
}) {
  return (
    <div className={`flex items-center gap-2.5 ${compact ? '' : 'mb-4'}`}>
      <span className="font-mono text-xs font-semibold text-[#0071e3]">
        {number}
      </span>
      <span className="text-muted-foreground [&_svg]:size-4">{icon}</span>
      <h2 className="text-sm font-semibold">{title}</h2>
    </div>
  );
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 py-3">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="max-w-40 truncate text-right font-medium">{value}</dd>
    </div>
  );
}
