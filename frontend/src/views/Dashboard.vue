<template>
  <div class="dashboard-viewport">
    <div class="dashboard-fixed" :style="fixedScreenStyle">
  <div class="dashboard-background">
    <Layout>
      <!-- 左侧区域：4个ECharts图表 (20%) -->
      <template #left>
        <LayoutPanel title="数据统计" class="left-panel-wrapper">
          <div class="left-panel">
            <!-- 时间范围选择器：真实可点击的按钮组 -->
            <div class="time-range-bar">
              <div class="range-buttons">
                <button
                  v-for="opt in RANGE_OPTIONS"
                  :key="opt.key"
                  class="range-btn"
                  :class="{ active: selectedRangeType === opt.key }"
                  @click="handleSelectRange(opt.key)"
                >{{ opt.label }}</button>
              </div>
              <a-range-picker
                v-if="selectedRangeType === 'custom'"
                v-model="customRange"
                show-time
                size="small"
                style="margin-top: 8px; width: 100%"
                @change="handleCustomRangeChange"
              />
              <div class="range-hint">{{ rangeHint }}</div>
            </div>

            <div class="charts-grid">
              <div class="chart-card">
                <div class="card-title">体长分布 (cm)</div>
                <div ref="lengthChartRef" class="chart-container"></div>
              </div>
              <div class="chart-card">
                <div class="card-title">体重分布 (g)</div>
                <div ref="weightChartRef" class="chart-container"></div>
              </div>
              <div class="chart-card">
                <div class="card-title">肥满度分布</div>
                <div ref="conditionChartRef" class="chart-container"></div>
              </div>
              <div class="chart-card">
                <div class="card-title">体长/体重比分布</div>
                <div ref="ratioChartRef" class="chart-container"></div>
              </div>
            </div>
            <div
              v-if="isResultMode && resultFishList.length === 0 && !resultLoading"
              class="result-stats-empty"
            >
              <a-empty description="该视频暂无鱼体数据，统计图等待算法结果。" />
            </div>
          </div>
        </LayoutPanel>
      </template>

      <!-- 中间区域：视频播放 (60%) -->
      <template #middle>
        <LayoutPanel title="实时检测分析" class="center-panel-wrapper">
          <div class="center-panel">
          <!-- 顶部信息栏 -->
          <div class="top-info-bar">
            <div class="info-left">
              <div class="info-item">
                <span class="label">开始时间：</span>
                <span class="value">{{ startTime || '--' }}</span>
              </div>
              <div class="info-item">
                <span class="label">检测耗时：</span>
                <span class="value">{{ duration }}</span>
              </div>
            </div>
            <div class="info-right">
              <div class="info-item">
                <span class="label">结束时间：</span>
                <span class="value">{{ endTime || '--' }}</span>
              </div>
              <div class="info-item">
                <span class="label">视频时长：</span>
                <span class="value">{{ videoDurationLabel }}</span>
              </div>
            </div>
          </div>

          <!-- 状态卡片 -->
          <div class="status-cards">
            <!-- <div class="status-card">
              <div class="status-icon"><i class="fa-solid fa-fish"></i></div>
              <div class="status-content">
                <div class="status-label">共计鱼条数</div>
                <div class="status-value">{{ totalFish }} <small>条</small></div>
              </div>
            </div>
            <div class="status-card">
              <div class="status-icon"><i class="fa-solid fa-spinner"></i></div>
              <div class="status-content">
                <div class="status-label">当前检测进度</div>
                <div class="status-value">{{ progress }}% <small>{{ progressText }}</small></div>
              </div>
            </div> -->
            <div
              v-if="cameraStreamActive || cameraState === 'starting' || cameraState === 'running'"
              class="status-card"
            >
              <div class="status-icon"><i class="fa-solid fa-video"></i></div>
              <div class="status-content">
                <div class="status-label">摄像头状态</div>
                <div class="status-value camera-status" :class="cameraConnected ? 'connected' : 'waiting'">
                  {{ cameraStateText(cameraState) }}
                </div>
              </div>
            </div>
            <div
              v-if="timerPending || timedRecording || stopReason || recordDurationSeconds"
              class="status-card timed-recording-card"
              :class="timerPending ? 'is-pending' : (timedRecording ? 'is-running' : (autoStopTriggered ? 'is-auto-stopped' : 'is-manual-stopped'))"
            >
              <div class="status-icon">
                <i :class="timerPending ? 'fa-solid fa-spinner fa-spin' : (timedRecording ? 'fa-solid fa-hourglass-half' : (autoStopTriggered ? 'fa-solid fa-circle-check' : 'fa-solid fa-circle-stop'))"></i>
              </div>
              <div class="status-content">
                <div class="status-label">
                  {{ timerPending ? '摄像头启动中' : (timedRecording ? '定时录制中' : (autoStopTriggered ? '已按计划自动停止' : '已手动停止')) }}
                </div>

                <!-- 启动等待中（timer_pending）：不显示递减倒计时 -->
                <div v-if="timerPending" class="status-value timed-pending">
                  <div class="timed-pending-desc">
                    正在等待树莓派推流成功，录制倒计时将在收到首帧后开始。
                  </div>
                  <div class="timed-row">
                    <span class="k">计划时长</span>
                    <span class="v">{{ formatSeconds(recordDurationSeconds) }}</span>
                  </div>
                  <div class="timed-row">
                    <span class="k">剩余时间</span>
                    <span class="v">--</span>
                  </div>
                </div>

                <!-- 录制中 -->
                <div v-else-if="timedRecording" class="status-value timed-running">
                  <div class="timed-remaining">
                    <span class="remaining-label">剩余时间</span>
                    <span class="remaining-value">{{ formatSeconds(remainingSeconds) }}</span>
                  </div>
                  <div class="timed-progress" v-if="timedProgressPercent !== null">
                    <div class="timed-progress-bar">
                      <div class="timed-progress-fill" :style="{ width: timedProgressPercent + '%' }"></div>
                    </div>
                    <div class="timed-progress-text">
                      <span>已完成 {{ timedProgressPercent }}%</span>
                      <span>剩余 {{ Math.max(0, 100 - timedProgressPercent) }}%</span>
                    </div>
                  </div>
                  <div class="timed-row"><span class="k">计划时长</span><span class="v">{{ formatSeconds(recordDurationSeconds) }}</span></div>
                  <div class="timed-row"><span class="k">预计停止</span><span class="v">{{ scheduledStopAt ? formatChinaDateTime(scheduledStopAt) : '--' }}</span></div>
                </div>

                <!-- 已停止 -->
                <div v-else class="status-value timed-stopped">
                  <div class="timed-stopped-desc">
                    {{ autoStopTriggered
                      ? '系统已在设定时间到达后自动停止录制，视频已提交后续处理。'
                      : '用户已手动结束本次录制。' }}
                  </div>
                  <div class="timed-row" v-if="stopReason">
                    <span class="k">停止原因</span>
                    <span class="v">{{ stopReason }}</span>
                  </div>
                  <div class="timed-row" v-if="stopSource">
                    <span class="k">停止来源</span>
                    <span class="v">{{ stopSource }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- 视频播放区 -->
          <div class="video-area">
            <div class="video-wrapper">
              <!--
                渲染优先级（从高到低）：
                  1. 摄像头实时输入（isCameraLive 或 cameraState in starting）
                     → 用户主动开始"摄像头输入"时，必须显示实时摄像头画面，不能被
                       result mode 抢占。
                  2. result 模式（从视频历史 / 报表跳转大屏进入）
                     → 显示算法标注视频（grounded_sam.mp4）。
                  3. 普通 upload / history 视频（VideoWall）
                  4. idle → VideoWall
              -->
              <template v-if="isCameraLive || cameraState === 'starting' || cameraState === 'running'">
                <div class="camera-stream-container">
                  <img
                    :src="cameraFrameUrl"
                    alt="Camera Stream"
                    class="camera-stream-img"
                    @click="handleCameraFrameClick"
                    @error="onCameraFrameError"
                  />
                  <div class="camera-statusbar">
                    <div class="status">
                      <span class="k">视频流：</span>
                      <span class="v">{{ streamUrl }}</span>
                    </div>
                    <div class="status">
                      <span class="k">状态：</span>
                      <span class="v">{{ cameraStateText(cameraState) }}</span>
                    </div>
                  </div>
                </div>
              </template>

              <template v-else-if="isResultMode">
                <div v-if="resultLoading" class="video-status-text">加载算法结果中...</div>
                <div v-else-if="resultError" class="video-status-text error">
                  {{ resultError }}
                </div>
                <div
                  v-else-if="resultGroundedUrl && !resultVideoErrored"
                  class="result-video-wrap"
                >
                  <video
                    :src="resultGroundedUrl"
                    controls
                    autoplay
                    muted
                    loop
                    class="grounded-sam-video"
                    @error="onResultVideoError"
                    @loadeddata="onResultVideoLoaded"
                  />
                  <div class="result-video-meta">
                    <span class="label">结果视频：</span>
                    <span class="meta-value">{{ resultVideoId }}</span>
                  </div>
                </div>
                <div v-else-if="resultVideoErrored" class="video-status-text error">
                  结果视频加载失败：<br />
                  {{ resultGroundedUrl || '(空 URL)' }}<br />
                  请检查 grounded_sam.mp4 是否为 H.264 / yuv420p 编码。
                </div>
                <div v-else class="video-status-text">
                  该视频暂无结果视频（grounded_sam.mp4）。
                </div>
              </template>

              <VideoWall
                v-else
                ref="videoWallRef"
                @fish-selected="handleFishSelected"
                @upload-success="handleUploadSuccess"
              />
            </div>
          </div>

          <!-- 底部操作按钮 -->
          <div class="control-buttons">
            <div class="camera-group">
              <button
                class="ctrl-btn camera-btn"
                :disabled="cameraState === 'stopping' || cameraStarting || cameraState === 'starting'"
                @click="handleCameraInput"
              >
                <i :class="cameraButtonIcon"></i>
                <span>{{ cameraButtonText }}</span>
              </button>
              <button class="ctrl-btn settings-btn" @click="handleOpenApertureSettings">
                <i class="fa-solid fa-gear"></i>
                <span>光圈设置</span>
              </button>
            </div>
            <!-- <button class="ctrl-btn upload-btn" :disabled="!canStart" @click="handleUpload">
              <i class="fa-solid fa-upload"></i>
              <span>上传</span>
            </button>
            <button class="ctrl-btn" :disabled="cameraStreamActive || !currentVideoId" @click="handlePlay">
              <i class="fa-solid fa-play"></i>
              <span>播放</span>
            </button>
            <button class="ctrl-btn" :disabled="cameraStreamActive || !currentVideoId" @click="handlePause">
              <i class="fa-solid fa-pause"></i>
              <span>暂停</span>
            </button>
            <button class="ctrl-btn detect-btn" :disabled="!canStart" @click="handleReDetect">
              <i class="fa-solid fa-magnifying-glass"></i>
              <span>重新检测</span>
            </button>
            <button class="ctrl-btn clear-btn" @click="handleClearCurrentTask">
              <i class="fa-solid fa-eraser"></i>
              <span>清除当前任务</span>
            </button> -->

            <a-modal
              v-model:visible="apertureDialogVisible"
              title="光圈设置"
              :width="840"
              :mask-closable="true"
              :footer="false"
              :unmount-on-close="false"
              @cancel="closeApertureDialog"
            >
              <div class="aperture-dialog">
                <div class="aperture-preview">
                  <div v-if="aperturePreviewLoading" class="aperture-preview-state">
                    <i class="fa-solid fa-spinner fa-spin"></i>
                    <span>正在启动摄像头预览...</span>
                  </div>
                  <div
                    v-else-if="aperturePreviewError"
                    class="aperture-preview-state error"
                  >
                    <i class="fa-solid fa-triangle-exclamation"></i>
                    <span>{{ aperturePreviewError }}</span>
                  </div>
                  <div
                    v-else-if="!aperturePreviewActive"
                    class="aperture-preview-state"
                  >
                    <i class="fa-solid fa-video-slash"></i>
                    <span>摄像头未启动，无法预览</span>
                  </div>
                  <img
                    v-show="aperturePreviewActive && !aperturePreviewError"
                    :src="apertureFrameUrl"
                    class="aperture-frame-img"
                    alt="光圈预览"
                    @error="onApertureFrameError"
                  />
                  <div class="aperture-preview-tip">
                    预览为实时 JPEG 抓帧，不会保存为视频，也不会进入视频历史记录
                  </div>
                </div>

                <div class="aperture-controls">
                  <div
                    class="aperture-mode-tip"
                    :class="`aperture-mode-${aperturePreviewMode}`"
                  >
                    <i v-if="aperturePreviewMode === 'preview'" class="fa-solid fa-eye"></i>
                    <i v-else-if="aperturePreviewMode === 'recording-reuse'" class="fa-solid fa-link"></i>
                    <i v-else class="fa-solid fa-circle-info"></i>
                    <span v-if="aperturePreviewMode === 'preview'">
                      当前为临时预览模式，关闭弹窗后会停止摄像头预览，不会保存视频。
                    </span>
                    <span v-else-if="aperturePreviewMode === 'recording-reuse'">
                      当前正在录制，预览复用正在录制的摄像头画面。关闭弹窗不会停止录制。
                    </span>
                    <span v-else>
                      正在检测摄像头状态...
                    </span>
                  </div>
                  <div class="aperture-section">
                    <div class="aperture-section-title">
                      树莓派光圈服务
                      <a-tag :color="apertureLedStatusColor" size="small">
                        {{ apertureLedStatusText }}
                      </a-tag>
                    </div>
                    <div class="aperture-section-desc">
                      {{ apertureLedStatusMessage || '通过后端代理访问树莓派，前端不直连' }}
                    </div>
                    <a-space>
                      <a-button
                        size="small"
                        :loading="apertureLedLoading"
                        @click="handleApertureLedPing"
                      >
                        检查服务
                      </a-button>
                      <a-button
                        type="primary"
                        size="small"
                        :loading="apertureLedActionLoading === 'start'"
                        @click="handleApertureLedStart"
                      >
                        开启光圈
                      </a-button>
                      <a-button
                        status="danger"
                        size="small"
                        :loading="apertureLedActionLoading === 'off'"
                        @click="handleApertureLedOff"
                      >
                        关闭光圈
                      </a-button>
                    </a-space>
                  </div>

                  <a-divider style="margin: 12px 0" />

                  <div class="aperture-section">
                    <div class="aperture-section-title">常用颜色</div>
                    <div class="aperture-color-presets">
                      <button
                        v-for="preset in apertureLedColorPresets"
                        :key="preset.name"
                        type="button"
                        class="aperture-color-preset"
                        @click="applyApertureColorPreset(preset)"
                      >
                        <span
                          class="aperture-color-dot"
                          :style="{ backgroundColor: `rgb(${preset.r}, ${preset.g}, ${preset.b})` }"
                        ></span>
                        {{ preset.name }}
                      </button>
                    </div>
                  </div>

                  <div class="aperture-section">
                    <div class="aperture-section-title">RGB 颜色</div>
                    <div class="aperture-rgb-row">
                      <a-input-number
                        v-model="apertureLedColor.r"
                        :min="0"
                        :max="255"
                        placeholder="R"
                      />
                      <a-input-number
                        v-model="apertureLedColor.g"
                        :min="0"
                        :max="255"
                        placeholder="G"
                      />
                      <a-input-number
                        v-model="apertureLedColor.b"
                        :min="0"
                        :max="255"
                        placeholder="B"
                      />
                    </div>
                    <div
                      class="aperture-color-preview"
                      :style="{ backgroundColor: `rgb(${apertureLedColor.r}, ${apertureLedColor.g}, ${apertureLedColor.b})` }"
                    ></div>
                    <a-button
                      type="primary"
                      size="small"
                      :loading="apertureLedActionLoading === 'color'"
                      @click="handleApertureApplyColor"
                    >
                      应用颜色
                    </a-button>
                  </div>

                  <div class="aperture-section">
                    <div class="aperture-section-title">
                      亮度（{{ apertureLedBrightness }}）
                    </div>
                    <a-slider
                      v-model="apertureLedBrightness"
                      :min="0"
                      :max="255"
                      :step="1"
                    />
                    <a-button
                      type="primary"
                      size="small"
                      :loading="apertureLedActionLoading === 'brightness'"
                      @click="handleApertureApplyBrightness"
                    >
                      应用亮度
                    </a-button>
                    <div class="aperture-section-desc">亮度 0~255，推荐默认 32</div>
                  </div>
                </div>
                <div class="aperture-dialog-footer">
                  <a-button type="primary" @click="closeApertureDialog">
                    关闭
                  </a-button>
                </div>
              </div>
            </a-modal>

            <a-modal
              v-model:visible="timedRecordingDialogVisible"
              class="timed-recording-modal"
              title="设置录制时长"
              :ok-text="confirmButtonText"
              :cancel-text="'取消'"
              :mask-closable="false"
              :ok-button-props="{ disabled: durationErrorMessage !== null }"
              width="560px"
              @ok="confirmStartTimedRecording"
              @cancel="timedRecordingDialogVisible = false"
            >
              <div class="timed-dialog">
                <div class="timed-dialog-desc">
                  到达设定时间后，系统会自动停止录制并进入算法处理。
                </div>

                <div class="timed-dialog-tip">
                  <i class="fa-solid fa-circle-info"></i>
                  <span>录制过程中可以切换页面或刷新浏览器，后端会继续录制。</span>
                </div>

                <div class="timed-dialog-group">
                  <div class="group-label">测试</div>
                  <div class="group-buttons">
                    <button
                      v-for="opt in DURATION_GROUP_TEST"
                      :key="opt.minutes"
                      type="button"
                      class="preset-btn"
                      :class="{ active: selectedDurationMinutes === opt.minutes }"
                      @click="pickPresetDuration(opt.minutes)"
                    >
                      {{ opt.label }}
                    </button>
                  </div>
                </div>

                <div class="timed-dialog-group">
                  <div class="group-label">常用</div>
                  <div class="group-buttons">
                    <button
                      v-for="opt in DURATION_GROUP_COMMON"
                      :key="opt.minutes"
                      type="button"
                      class="preset-btn"
                      :class="{ active: selectedDurationMinutes === opt.minutes }"
                      @click="pickPresetDuration(opt.minutes)"
                    >
                      {{ opt.label }}
                    </button>
                  </div>
                </div>

                <div class="timed-dialog-group">
                  <div class="group-label">长时</div>
                  <div class="group-buttons">
                    <button
                      v-for="opt in DURATION_GROUP_LONG"
                      :key="opt.minutes"
                      type="button"
                      class="preset-btn"
                      :class="{ active: selectedDurationMinutes === opt.minutes, cap: opt.cap }"
                      @click="pickPresetDuration(opt.minutes)"
                    >
                      {{ opt.label }}
                      <span v-if="opt.cap" class="cap-tag">上限</span>
                    </button>
                  </div>
                </div>

                <a-divider style="margin: 14px 0" />

                <div class="timed-dialog-custom">
                  <div class="label">自定义时长</div>
                  <div class="custom-row">
                    <a-input-number
                      v-model="customDurationHours"
                      :min="0"
                      :max="24"
                      :step="1"
                      :precision="0"
                      placeholder="小时"
                    />
                    <span class="sep">小时</span>
                    <a-input-number
                      v-model="customDurationMinutes"
                      :min="0"
                      :max="59"
                      :step="5"
                      :precision="0"
                      placeholder="分钟"
                    />
                    <span class="sep">分钟</span>
                  </div>
                  <div class="custom-summary">
                    当前选择：<span class="summary-value">{{ customDurationReadable }}</span>
                  </div>
                  <div v-if="durationErrorMessage" class="timed-dialog-error">
                    <i class="fa-solid fa-circle-exclamation"></i>
                    <span>{{ durationErrorMessage }}</span>
                  </div>
                </div>
              </div>
            </a-modal>
            <a-modal
              v-model:visible="calibrationVisible"
              title="摄像头视角标定"
              :footer="false"
              width="760px"
              :ok-text="'关闭'"
              @cancel="calibrationVisible = false"
            >
            
              <div class="calibration-panel">
                <div class="calibration-status-row">
                  <span class="label">当前状态：</span>
                  <a-tag :color="calibrationStatus?.exists ? 'green' : 'orange'">
                    {{ calibrationStatusText }}
                  </a-tag>
                </div>

                <div class="calibration-desc">
                  本功能从当前摄像头画面拍摄一张标定帧，并调用
                  <code>algorithms.core.adjust_calibration</code> 生成
                  <code>data/calibration/current_calibration.json</code>。
                  视频处理前必须先完成相机标定。
                </div>

                <a-divider />

                <!-- 步骤 1：开启摄像头 + 实时预览 -->
                <div class="calibration-block">
                  <div class="label">① 开启摄像头</div>
                  <div class="calibration-hint">
                    从主视频区点"摄像头输入"按钮，或在此处直接开启同一路摄像头。
                  </div>
                  <div class="calibration-actions">
                    <a-button
                      type="secondary"
                      :loading="cameraStarting"
                      :disabled="cameraState === 'running' || cameraState === 'starting'"
                      @click="handleStartCalibrationCamera"
                    >
                      <i class="fa-solid fa-video"></i>
                      <span style="margin-left: 6px">开启摄像头</span>
                    </a-button>
                    <a-button
                      status="danger"
                      :disabled="!isCameraLive"
                      @click="handleStopCalibrationCamera"
                    >
                      <i class="fa-solid fa-stop"></i>
                      <span style="margin-left: 6px">关闭摄像头</span>
                    </a-button>
                  </div>
                  <div v-if="isCameraLive" class="calibration-preview">
                    <img :src="calibrationFrameUrl" alt="calibration-frame" />
                  </div>
                  <div v-else class="calibration-hint">
                    摄像头未开启，等待开启中…
                  </div>
                </div>

                <a-divider />

                <!-- 步骤 2：拍摄标定帧 + 执行标定 -->
                <div class="calibration-block">
                  <div class="label">② 拍摄标定帧并执行标定</div>
                  <div class="calibration-hint">
                    从摄像头取当前帧，保存到
                    <code>data/uploads/calibration/YYYY/MM/DD/</code>，
                    随后执行 adjust_calibration CLI 生成透视矩阵。
                  </div>
                  <a-button
                    type="primary"
                    :loading="calibrationLoading"
                    @click="handleCaptureAndCalibrate"
                  >
                    <i class="fa-solid fa-camera-retro"></i>
                    <span style="margin-left: 6px">
                      {{
                        calibrationLoading
                          ? '正在连接摄像头并拍摄标定帧...'
                          : '拍摄标定帧并执行标定'
                      }}
                    </span>
                  </a-button>
                  <div v-if="calibrationLastResult?.source_image_path" class="calibration-path">
                    <span class="label">最近标定帧：</span>
                    <code>{{ calibrationLastResult.source_image_path }}</code>
                  </div>
                </div>

                <a-divider />

                <!-- 步骤 3：查看矫正效果 -->
                <div v-if="calibrationLastResult" class="calibration-block">
                  <div class="label">③ 查看矫正效果</div>
                  <div class="calibration-preview-grid">
                    <div class="calibration-preview-cell">
                      <div class="cell-title">标定原图</div>
                      <img
                        v-if="calibrationLastResult.source_image_url"
                        :src="calibrationLastResult.source_image_url"
                        alt="source"
                      />
                      <div v-else class="calibration-hint">尚未拍摄</div>
                    </div>
                    <div class="calibration-preview-cell">
                      <div class="cell-title">矫正预览图</div>
                      <img
                        v-if="calibrationLastResult.adjusted_preview_url"
                        :src="calibrationLastResult.adjusted_preview_url"
                        alt="adjusted-preview"
                      />
                      <div v-else-if="calibrationLastResult.calibrated" class="calibration-hint">
                        标定完成，但未找到矫正预览图。请检查
                        <code>data/calibration/sessions/</code> 或算法日志。
                      </div>
                      <div v-else class="calibration-hint">尚未标定</div>
                    </div>
                  </div>
                  <div v-if="calibrationLastResult.calibration_json" class="calibration-path">
                    <span class="label">calibration JSON：</span>
                    <code>{{ calibrationLastResult.calibration_json }}</code>
                  </div>
                  <div v-if="calibrationLastResult.log_path" class="calibration-path">
                    <span class="label">日志：</span>
                    <code>{{ calibrationLastResult.log_path }}</code>
                  </div>
                  <div v-if="calibrationLastResult.adjusted_preview_warning" class="calibration-warn">
                    {{ calibrationLastResult.adjusted_preview_warning }}
                  </div>
                </div>

                <a-divider />

                <!-- 备用方式：手动服务器路径 -->
                <a-collapse>
                  <a-collapse-item header="备用方式：使用服务器已有标定图片" key="manual">
                    <div class="calibration-hint">
                      当摄像头不可用时，可以填写服务器上已有的标定图片绝对路径作为 fallback。
                      一般情况请使用上面的"摄像头拍摄 + 标定"主流程。
                    </div>
                    <a-input
                      v-model="calibrationImagePath"
                      placeholder="例如：/home/dsl/.../data/calibration/tmp/calib.png"
                      allow-clear
                    />
                    <div class="calibration-actions" style="margin-top: 8px;">
                      <a-button
                        type="secondary"
                        :loading="calibrationLoading"
                        :disabled="!calibrationImagePath"
                        @click="handleRunCalibrationManual"
                      >
                        使用该路径执行标定
                      </a-button>
                    </div>
                  </a-collapse-item>
                </a-collapse>

                <a-divider />

                <div v-if="calibrationRawJson" class="calibration-json">
                  <div class="label">current_calibration.json</div>
                  <pre>{{ calibrationRawJson }}</pre>
                </div>
              </div>
            </a-modal>
          </div>
        </div>
        </LayoutPanel>
      </template>

      <!-- 右侧区域：鱼 ID 检索 (20%) -->
      <template #right>
        <LayoutPanel title="鱼 ID 检索" class="right-panel-wrapper">
        <div class="right-fish-search-panel">
          <section class="chart-card fish-query-card">
            <div class="card-title">检索条件</div>
            <div class="query-row">
              <label class="query-label">日期</label>
              <a-date-picker
                v-model="fishQueryDate"
                value-format="YYYY-MM-DD"
                allow-clear
                style="flex:1"
                @change="onFishQueryDateChange"
              />
            </div>
            <div class="query-row">
              <label class="query-label">视频信息</label>
              <a-select
                v-model="fishQueryVideoId"
                placeholder="请选择视频"
                allow-search
                allow-clear
                style="flex:1"
                @change="onFishQueryVideoChange"
              >
                <a-option
                  v-for="v in fishQueryVideos"
                  :key="v.video_id"
                  :value="v.video_id"
                >
                  {{ v.label || v.video_id }}
                </a-option>
              </a-select>
            </div>
            <div class="query-row">
              <label class="query-label">鱼 ID</label>
              <a-select
                v-model="fishQueryFishId"
                placeholder="请选择鱼 ID"
                allow-search
                allow-clear
                style="flex:1"
                @change="onFishQueryFishChange"
              >
                <a-option
                  v-for="f in fishQueryFishOptions"
                  :key="f.key"
                  :value="f.key"
                >
                  {{ f.label }}
                </a-option>
              </a-select>
            </div>
          </section>

          <section class="chart-card fish-image-card">
            <div class="card-title">鱼体图像</div>
            <div class="dashboard-fish-image-box">
              <img
                v-if="selectedDashboardFishImageUrl"
                :src="selectedDashboardFishImageUrl"
                :style="dashboardFishImageStyle"
                alt="鱼体图像"
                @load="onDashboardFishImageLoad"
              />
              <div v-else class="fish-image-empty">
                请选择鱼 ID 查看鱼体图像
              </div>
            </div>
          </section>

          <section class="chart-card fish-metrics-card">
            <div class="card-title">鱼品质指标检测结果</div>
            <!--
              优先级：
                1. 实时摄像头录制中：显示提示，不显示旧鱼指标（避免与 result mode 残留混淆）
                2. 选中鱼：显示鱼指标
                3. 否则：empty 提示
            -->
            <div
              v-if="isCameraLive || cameraState === 'starting' || cameraState === 'running'"
              class="fish-metrics-camera-notice"
            >
              <i class="fa-solid fa-circle-info"></i>
              <div class="notice-text">
                <div class="notice-title">实时摄像头录制中</div>
                <div class="notice-desc">
                  {{
                    timedRecording
                      ? '本次定时录制进行中，录制结束后系统会自动入队算法处理。'
                      : '本次录制进行中，停止后系统会自动入队算法处理。'
                  }}
                  结果可在「视频历史记录」中查看，鱼体指标将在算法完成后显示。
                </div>
              </div>
            </div>
            <div v-else-if="selectedDashboardFish" class="fish-metrics-grid">
              <div class="metric-row">
                <span class="metric-label">视频编号</span>
                <span class="metric-value">{{ fishQueryVideoId || '--' }}</span>
              </div>
              <div class="metric-row">
                <span class="metric-label">鱼 ID</span>
                <span class="metric-value">{{ selectedDashboardFishShortId }}</span>
              </div>
              <div class="metric-row">
                <span class="metric-label">是否完整</span>
                <span class="metric-value">{{ selectedDashboardFishStatusText }}</span>
              </div>
              <div class="metric-row">
                <span class="metric-label">体长</span>
                <span class="metric-value">{{ fmt(selectedDashboardFishLength, 2) }} <small>cm</small></span>
              </div>
              <div class="metric-row">
                <span class="metric-label">无尾体长</span>
                <span class="metric-value">{{ fmt(selectedDashboardFishNoTail, 2) }} <small>cm</small></span>
              </div>
              <div class="metric-row">
                <span class="metric-label">体高</span>
                <span class="metric-value">{{ fmt(selectedDashboardFishHeight, 2) }} <small>cm</small></span>
              </div>
              <div class="metric-row">
                <span class="metric-label">体重</span>
                <span class="metric-value">{{ fmt(selectedDashboardFishWeight, 2) }} <small>g</small></span>
              </div>
              <div class="metric-row">
                <span class="metric-label">肥满度</span>
                <span class="metric-value">{{ fmt(selectedDashboardFishCf, 3) }}</span>
              </div>
              <div class="metric-row">
                <span class="metric-label">破损率</span>
                <span class="metric-value damage">{{ selectedDashboardFishDamage }}<small>%</small></span>
              </div>
            </div>
            <a-empty v-else description="请选择鱼 ID 查看检测结果" />
          </section>
        </div>
        </LayoutPanel>
      </template>
    </Layout>
  </div>
  </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, onErrorCaptured, computed, reactive, nextTick, watch } from 'vue'
import Layout from '@/layout/Layout.vue'
import { LayoutPanel } from '@/layout'
import VideoWall from '@/components/stream/VideoWall.vue'
// 注：Dashboard 不再打开"摄像头设置"弹窗。
// 摄像头设置功能已迁移到 系统设置 → 摄像头设置（admin/system/CameraSettingsPage.vue）。
// CameraSettings.vue 保留作为旧调用方兼容，本阶段此处不再 import。
import * as echarts from 'echarts'
import type { FishDetail } from '@/types/fish'
import dayjsLib from 'dayjs'
const dayjs = dayjsLib
import { Message } from '@arco-design/web-vue'
import { http } from '@/api/http'
import { setDistributionChartOption } from '@/utils/distributionChart'
import { useFixedScreen } from '@/utils/useFixedScreen'
import { getVideoState } from '@/store/video'
import { useRoute, useRouter } from 'vue-router'
import { getAlgorithmVideoResult } from '@/api/algorithm'
import {
  pingLed,
  startLed,
  offLed,
  setLedColor,
  setLedBrightness,
} from '@/api/led'
// 摄像头/上传的运行时数据
import { cameraStateText } from '@/utils/status'
import { formatChinaDateTime } from '@/utils/time'
import { normalizeReportUrl } from '@/utils/reportPath'
import { logUserAction } from '@/utils/operationLogger'
import {
  getAlgorithmCalibrationStatus,
  runAlgorithmCalibration,
  captureAndRunCalibration,
} from '@/api/algorithm'
import {
  getFishImageUrl,
  getFishLengthCm,
  getFishNoTailLengthCm,
  getFishHeightCm,
  getFishWeightG,
  getFishDamageRatio,
  getFishDamagePercent,
  getFishConditionFactor,
  getFishErrorMessage,
  getFishDisplayId,
} from '@/utils/fishFields'

const videoWallRef = ref<InstanceType<typeof VideoWall> | null>(null)

const route = useRoute()
const router = useRouter()

// result 模式：从 ?mode=result&video_id=xxx 进入。
// 注意：所有 ref / computed / function 必须先声明，再被 watch 使用，
// 否则 setup 执行时会触发 "can't access lexical declaration ... before initialization"。
const isResultMode = computed(
  () => route.query.mode === 'result' && !!String(route.query.video_id || '')
)
const resultVideoId = computed(() => String(route.query.video_id || ''))
const resultData = ref<any | null>(null)
const resultLoading = ref<boolean>(false)
const resultError = ref<string>('')
const resultTrackIdInput = ref<string>('')
// 用户当前选中的 track_id（字符串，独立于输入框状态）
const resultSelectedTrackId = ref<string>('')
// resultGroundedUrl / resultVideoErrored 在更下方 normalizeStaticUrl 附近声明。
// 注意：showCameraSettings 已删除。摄像头设置入口已迁移到 系统设置 → 摄像头设置。
const cameraStreamActive = ref(false)
const cameraFrameUrl = ref('')
const cameraConnected = ref(false)
const cameraState = ref<string>('idle')
let cameraFrameTimer: number | null = null
// 摄像头视角标定状态
const calibrationVisible = ref(false)
const calibrationLoading = ref(false)
const calibrationStatus = ref<any | null>(null)
const calibrationImagePath = ref<string>('')
const calibrationRawJson = ref<string>('')
const calibrationLastResult = ref<any | null>(null)
const cameraStarting = ref<boolean>(false)
const calibrationFrameTs = ref<number>(Date.now())
const calibrationFrameUrl = computed(
  () => `${apiDomainForFrame()}/api/camera_yzt/frame?t=${calibrationFrameTs.value}`
)
let calibrationFrameTimer: number | null = null

// ---- 光圈设置弹窗 + 摄像头预览（Dashboard 底部"光圈设置"按钮接入） ----
//
// 设计约束：
//   - preview 是预览，不创建 video_task，不写视频，不入队算法。
//   - preview 关闭时主动调 /api/camera_yzt/preview/stop 释放推流。
//   - 主"摄像头输入"在跑时拒绝启动 preview。
//   - 不影响主摄像头录制定时录制 / 标定抓拍 / 视频历史。
const apertureDialogVisible = ref(false)
const aperturePreviewLoading = ref(false)
const aperturePreviewActive = ref(false)
// 标记：preview 是否由本弹窗自己启动。
// 用来保证 onUnmounted / close 时不会误停由"主摄像头输入"启动的录制。
const aperturePreviewStartedByDialog = ref(false)
// 当前光圈弹窗的预览模式：
//   - 'preview'           弹窗自己启的临时 preview（关闭会停 preview）
//   - 'recording-reuse'   复用了主"摄像头输入"的推流（关闭不会停主录制）
//   - 'unknown'           拉 status 失败 / 初始探测中
const aperturePreviewMode = ref<'preview' | 'recording-reuse' | 'unknown'>('unknown')
const aperturePreviewError = ref('')
const apertureFrameTs = ref<number>(Date.now())
const apertureFrameUrl = computed(
  () => `${apiDomainForFrame()}/api/camera_yzt/frame?t=${apertureFrameTs.value}`,
)
let apertureFrameTimer: number | null = null

// 光圈 LED 控制状态（参考 CameraSettingsPanel.vue 中光圈设置 tab）
const apertureLedStatus = ref<'unknown' | 'online' | 'offline'>('unknown')
const apertureLedStatusMessage = ref('')
const apertureLedLoading = ref(false)
const apertureLedActionLoading = ref<string>('')
const apertureLedColor = reactive({ r: 255, g: 255, b: 255 })
const apertureLedBrightness = ref<number>(32)
const apertureLedColorPresets = [
  { name: '白光', r: 255, g: 255, b: 255 },
  { name: '暖白', r: 255, g: 220, b: 180 },
  { name: '红', r: 255, g: 0, b: 0 },
  { name: '绿', r: 0, g: 255, b: 0 },
  { name: '蓝', r: 0, g: 0, b: 255 },
]

const apertureLedStatusText = computed(() => {
  if (apertureLedStatus.value === 'online') return '在线'
  if (apertureLedStatus.value === 'offline') return '离线'
  return '未知'
})
const apertureLedStatusColor = computed(() => {
  if (apertureLedStatus.value === 'online') return 'green'
  if (apertureLedStatus.value === 'offline') return 'red'
  return 'gray'
})

// ---- 定时录制（前端只做展示 + 透传 duration_minutes） ----
const timedRecordingDialogVisible = ref(false)
const selectedDurationMinutes = ref<number>(60)
const customDurationHours = ref<number>(1)
const customDurationMinutes = ref<number>(0)

// 服务端 /status 是权威值；本地每秒递减仅做 UI 平滑。
const recordDurationSeconds = ref<number | null>(null)
const scheduledStopAt = ref<string | null>(null)
const remainingSeconds = ref<number | null>(null)
const timedRecording = ref<boolean>(false)
// 后端"延迟武装"标志：duration 已设置但首帧未到，scheduled_stop_at 为空。
// 此时不递减倒计时，状态卡显示"摄像头启动中"。
const timerPending = ref<boolean>(false)
const autoStopTriggered = ref<boolean>(false)
const stopReason = ref<string>('')
const stopSource = ref<string>('')
let remainingTickTimer: number | null = null

function calcSelectedDurationMinutes(): number {
  const h = Number(customDurationHours.value || 0)
  const m = Number(customDurationMinutes.value || 0)
  if (!Number.isFinite(h) || !Number.isFinite(m)) return NaN
  return h * 60 + m
}

const durationErrorMessage = computed<string | null>(() => {
  const m = calcSelectedDurationMinutes()
  if (!Number.isFinite(m) || m <= 0) {
    return '录制时长必须大于 0 分钟'
  }
  if (m > 1440) {
    return '录制时长不能超过 24 小时'
  }
  return null
})

// 弹窗“当前选择：X 小时 Y 分钟（共 N 分钟）”展示用
const customDurationReadable = computed(() => {
  const m = calcSelectedDurationMinutes()
  const h = Math.floor((Number.isFinite(m) ? m : 0) / 60)
  const mm = Math.floor((Number.isFinite(m) ? m : 0) % 60)
  return `${h} 小时 ${mm} 分钟（共 ${Number.isFinite(m) ? m : 0} 分钟）`
})

// 录制中状态卡：进度条百分比
// 后端 remaining_seconds 是权威值，进度 = 已消耗 / 计划时长
const timedProgressPercent = computed<number | null>(() => {
  if (!timedRecording.value) return null
  const total = recordDurationSeconds.value
  const remain = remainingSeconds.value
  if (
    total === null || total === undefined ||
    remain === null || remain === undefined
  ) return null
  if (!Number.isFinite(total) || !Number.isFinite(remain) || total <= 0) return null
  const consumed = Math.max(0, total - remain)
  const pct = Math.round((consumed / total) * 100)
  return Math.max(0, Math.min(100, pct))
})

// 弹窗底部"开始录制"按钮文案：合法时附加"将录制 X 分钟"
const confirmButtonText = computed(() => {
  const m = calcSelectedDurationMinutes()
  if (!Number.isFinite(m) || m <= 0 || m > 1440) return '开始录制'
  if (m < 60) return `开始录制（${m} 分钟）`
  const h = Math.floor(m / 60)
  const mm = m % 60
  if (mm === 0) return `开始录制（${h} 小时）`
  return `开始录制（${h} 小时 ${mm} 分钟）`
})

const calibrationEnabled = computed(() => {
  return Boolean(calibrationStatus.value?.exists)
})

const calibrationStatusText = computed(() => {
  if (!calibrationStatus.value?.exists) return '未标定'
  return '已标定'
})

// 摄像头/上传的运行时配置（来源：CameraSettings 弹窗）
// 默认不写死推流地址，由后端按主机局域网 IP 自动生成。
const cameraConfig = reactive({
  stream_url: '',
  width: 1920,
  height: 1080,
  fps: 25,
})

const streamUrl = computed(() => cameraConfig.stream_url)

// ---- 时间字段（持久化） ----
const startTime = ref<string | null>(null)         // 检测开始时间（video_task.started_at/created_at）
const endTime = ref<string | null>(null)           // 检测结束时间（video_task.finished_at）
const duration = ref<string>('00:00:00')           // 检测耗时 = finished - started（或 now - started）
const videoDurationLabel = ref<string>('--')      // 视频文件真实时长（与检测耗时分开）

// ---- 任务状态 ----
const currentVideoId = ref<string>('')
const currentSourceType = ref<string>('')
const currentVideoStatus = ref<string>('')
const currentAlgorithm = ref<string>('')
const totalFish = ref(0)
const processedFish = ref(0)
const progress = ref(0)
const lastError = ref<string | null>(null)
const videoDurationSeconds = ref<number | null>(null)

// const selectedFish = ref<FishDetail | null>(null)
// 待选鱼条目（点击视频画面时临时存储，等待 selectFishFromFrame 接口返回结果后正式存储到 selectedFish）
const selectedFish = ref<any | null>(null)

// result 模式 fish 列表 + 选中 fish computed
const resultFishList = computed<any[]>(() => {
  const data: any = resultData.value
  if (!data) return []
  return Array.isArray(data.fish) ? data.fish : []
})
const resultTrackIdList = computed<any[]>(() =>
  resultFishList.value.map((f: any) => f?.track_id).filter((x: any) => x !== null && x !== undefined)
)
const resultTotalFish = computed(() => resultFishList.value.length)
const resultCompleteFish = computed(
  () => resultFishList.value.filter((f: any) => (f?.status || '').toLowerCase() === 'done').length
)
const resultIncompleteFish = computed(
  () => resultFishList.value.filter((f: any) => (f?.status || '').toLowerCase() === 'incomplete').length
)
const resultFailedFish = computed(
  () => resultFishList.value.filter((f: any) => (f?.status || '').toLowerCase() === 'failed').length
)

// resultSearchedFish 改成 computed：避免 watch 在定义前访问 ref 导致初始化错误。
// 选择规则：resultSelectedTrackId 不为空时按它匹配；否则 resultTrackIdInput。
const resultSearchedFish = computed<any | null>(() => {
  const fishList = resultFishList.value
  const targetA = String(resultSelectedTrackId.value || '').trim()
  const targetB = String(resultTrackIdInput.value || '').trim()
  const target = targetA || targetB
  if (!target) return null
  return (
    fishList.find(
      (fish: any) => String(fish?.track_id ?? '').trim() === target
    ) || null
  )
})

const resultFishDamagePercent = computed(() => {
  const ratio: any = resultSearchedFish.value?.surface_damage_ratio
  if (ratio === null || ratio === undefined) return '--'
  const v = Number(ratio)
  if (!Number.isFinite(v)) return '--'
  return `${(v * 100).toFixed(2)}%`
})

const resultFishCropUrl = computed(() => {
  const fish: any = resultSearchedFish.value
  if (!fish) return ''
  return normalizeStaticUrl(fish.crop_image_url || fish.crop_image_path || '')
})

async function loadResultMode() {
  if (!isResultMode.value) {
    resultData.value = null
    return
  }
  const vid = resultVideoId.value
  resultLoading.value = true
  resultError.value = ''
  try {
    const resp = await getAlgorithmVideoResult(vid)
    if (resp.data?.data) {
      resultData.value = resp.data.data
    } else {
      resultError.value = '算法结果为空'
    }
  } catch (err: any) {
    resultError.value =
      err?.response?.data?.detail?.error ||
      err?.response?.data?.error ||
      err?.message ||
      '加载算法结果失败'
  } finally {
    resultLoading.value = false
  }
}

/**
 * 主动从 result mode（视频历史跳转 / 报表跳转大屏）切到实时摄像头模式。
 *
 * 只清理"展示层"的 result mode 状态：
 *   - 清 result 模式的视频/鱼数据/统计图
 *   - 清路由 query 中的 mode=result & video_id（旧视频 id），避免 route watcher
 *     反向把旧 grounded_sam / 旧鱼指标重新拉回来
 *   - 清右侧鱼 ID 检索面板的视频选择，避免继续展示旧视频的鱼指标
 *   - 清选中的鱼 / 消息
 *
 * 不动：
 *   - 不删数据库任何数据
 *   - 不影响主摄像头定时录制 / 光圈预览 / 标定状态
 *   - 不动 cameraConfig
 *   - 不动 localStorage
 *   - 不影响下一次用户从视频历史重新跳转（重新进 result mode 自然加载）
 */
function switchToCameraLiveMode() {
  // 1. 清 result 模式展示状态
  resultData.value = null
  resultError.value = ''
  resultLoading.value = false
  resultVideoErrored.value = false
  resultTrackIdInput.value = ''
  resultSelectedTrackId.value = ''
  // 当前选中的鱼（result 模式下选过鱼）
  selectedFish.value = null
  selectedFishMessage.value = ''

  // 2. 清左侧 4 个分布图数据
  try {
    chartData.length = []
    chartData.weight = []
    chartData.condition = []
    chartData.ratio = []
  } catch (e) {
    // chartData 可能尚未初始化（不会发生，但防御性 catch）
  }

  // 3. 清路由 query 中的 result 模式参数
  //    注意：只清 mode / video_id，其它 query 参数（如 tab）保留
  try {
    if (
      route &&
      route.query &&
      (route.query.mode !== undefined || route.query.video_id !== undefined)
    ) {
      const nextQuery: Record<string, any> = { ...route.query }
      delete nextQuery.mode
      delete nextQuery.video_id
      router.replace({ path: route.path, query: nextQuery })
    }
  } catch (e) {
    console.warn('[Dashboard switchToCameraLiveMode] clear route query failed:', e)
  }

  // 4. 清右侧鱼 ID 检索面板的视频选择（不直接清 fishQueryVideoId，避免
  //    watch 触发 loadDashboardFishQueryVideos 拉远端列表；
  //    这里只把 dashboard-fish 面板的"已选中鱼"清掉，让用户自己重新选）
  // 注意：selectedDashboardFish 是 derived，selectedFish 已清，指标面板会自动回到 empty
}

// 注意：resultVideoErrored / resultGroundedUrl / normalizeStaticUrl 在文件下方声明，
// watch 内部不直接访问它们。

// 注意：原「route query watch」和「resultFishList watch」已移到这个文件后面
// 「initCharts() / applyResultModeStats」声明之后再注册，因为这两个 immediate
// watch 在 setup() 阶段同步执行，会触发 TDZ：chartData / charts / initCharts
// 尚未初始化。这里只保留 searchFishByTrackId / setResultTrackId / resultFishStatusText
// / fmtNum 等不依赖 chartData 的辅助函数。

function searchFishByTrackId() {
  // resultSearchedFish 是 computed；只需把输入同步给 resultSelectedTrackId，
  // template 会自动响应并展示对应 fish。
  resultSelectedTrackId.value = String(resultTrackIdInput.value || '').trim()
}

function setResultTrackId(t: any) {
  const v = String(t ?? '')
  resultTrackIdInput.value = v
  resultSelectedTrackId.value = v.trim()
}

function getResultFishStatusColor(status: string | undefined | null): string {
  const s = String(status || '').toLowerCase()
  if (s === 'done') return 'green'
  if (s === 'incomplete') return 'orange'
  if (s === 'failed') return 'red'
  return 'gray'
}

function resultFishStatusText(status: string | undefined | null): string {
  const s = String(status || '').toLowerCase()
  if (s === 'done') return '完整鱼 / done'
  if (s === 'incomplete') return '不完整鱼 / incomplete'
  if (s === 'failed') return '计算失败 / failed'
  return status || '未知'
}

function fmtNum(v: any, digits = 2): string {
  if (v === null || v === undefined || v === '') return '--'
  const n = Number(v)
  if (!Number.isFinite(n)) return '--'
  return n.toFixed(digits)
}

function normalizeStaticUrl(path?: string | null): string {
  if (!path) return ''
  const p = String(path).trim()
  if (!p) return ''

  if (p.startsWith('http://') || p.startsWith('https://')) return p
  if (p.startsWith('/static/')) return p
  if (p.startsWith('static/')) return '/' + p

  if (p.startsWith('data/')) {
    return '/static/' + p.slice('data/'.length)
  }
  if (p.startsWith('/data/')) {
    return '/static/' + p.slice('/data/'.length)
  }

  if (
    p.startsWith('uploads/') ||
    p.startsWith('results/') ||
    p.startsWith('calibration/') ||
    p.startsWith('tmp/')
  ) {
    return '/static/' + p
  }

  if (
    p.startsWith('/uploads/') ||
    p.startsWith('/results/') ||
    p.startsWith('/calibration/') ||
    p.startsWith('/tmp/')
  ) {
    return '/static' + p
  }

  // 兼容绝对路径里嵌套 /data/ 的写法，例如 /home/.../data/results/...
  const dataIdx = p.indexOf('/data/')
  if (dataIdx >= 0) {
    return '/static/' + p.slice(dataIdx + '/data/'.length)
  }

  return p
}

const resultGroundedUrl = computed(() => {
  const data: any = resultData.value
  return normalizeStaticUrl(data?.grounded_video_url || data?.grounded_video || '')
})

const resultVideoErrored = ref<boolean>(false)
function onResultVideoError() {
  resultVideoErrored.value = true
}

function onResultVideoLoaded() {
  resultVideoErrored.value = false
}

const isCameraLive = computed(
  () =>
    cameraStreamActive.value ||
    cameraState.value === 'running' ||
    cameraState.value === 'starting'
)
const selectedFishMessage = ref('')

// 鱼 ID 检索（新）
const fishQueryDate = ref<string>(dayjsLib().format('YYYY-MM-DD'))
const fishQueryVideoId = ref<string>('')
const fishQueryFishId = ref<string>('')
const fishQueryVideos = ref<any[]>([])
const fishQueryFishes = ref<any[]>([])

function getDashboardFishOptionValue(fish: any): string {
  // 用于下拉框 value，确保唯一性（避免不同鱼展示相同短 ID 时选错）
  if (!fish) return ''
  return String(
    fish.fish_uid ??
      fish.fish_id ??
      fish.track_id ??
      fish.id ??
      fish.fishId ??
      '',
  )
}

function getShortFishId(fish: any): string {
  // 用于下拉框 label 和指标卡片显示：尽量短、尽量可读
  if (!fish) return '--'
  const trackId = fish.track_id
  if (
    trackId !== undefined &&
    trackId !== null &&
    String(trackId).trim() !== ''
  ) {
    return `鱼 ${String(trackId).trim()}`
  }
  const fishId = fish.fish_id
  if (
    fishId !== undefined &&
    fishId !== null &&
    String(fishId).trim() !== ''
  ) {
    const s = String(fishId).trim()
    if (!s) {
      // fallthrough
    } else {
      return s.length > 10 ? `鱼 ${s.slice(-6)}` : `鱼 ${s}`
    }
  }
  const uid = fish.fish_uid || fish.id || fish.fishId || ''
  const s = String(uid || '').trim()
  if (!s) return '--'
  return `鱼 ${s.length > 8 ? s.slice(-8) : s}`
}

const fishQueryFishOptions = computed(() => {
  const list = Array.isArray(fishQueryFishes.value) ? fishQueryFishes.value : []
  return list.map((f) => {
    const value = getDashboardFishOptionValue(f)
    const shortId = getShortFishId(f)
    const status = String(f.status || '').toLowerCase()
    const statusLabel =
      status === 'done'
        ? '完整'
        : status === 'incomplete'
        ? '不完整'
        : status === 'failed'
        ? '失败'
        : status || ''
    const label = statusLabel
      ? `${shortId}（${statusLabel}）`
      : shortId
    return { key: value, label, shortId }
  })
})

const selectedDashboardFish = computed(() => {
  const list = Array.isArray(fishQueryFishes.value) ? fishQueryFishes.value : []
  const id = String(fishQueryFishId.value || '')
  if (!id) return null
  return list.find((f) => getDashboardFishOptionValue(f) === id) || null
})

const selectedDashboardFishImageUrl = computed(() => {
  const f: any = selectedDashboardFish.value
  if (!f) return ''
  const candidate =
    f.crop_image_url || f.crop_image_path || f.corrected_image_url || f.corrected_image_path || ''
  return normalizeReportUrl(String(candidate))
})

const selectedDashboardFishShortId = computed(() => {
  const f: any = selectedDashboardFish.value
  if (!f) return '--'
  return getShortFishId(f) || '--'
})

const selectedDashboardFishStatusText = computed(() => {
  const f: any = selectedDashboardFish.value
  if (!f) return '--'
  const s = String(f.status || '').toLowerCase()
  if (s === 'done') return '完整'
  if (s === 'incomplete') return '不完整'
  if (s === 'failed') return '失败'
  return s || '--'
})

function _toNumOrNull(v: any): number | null {
  const n = Number(v)
  if (!Number.isFinite(n)) return null
  return n
}

const selectedDashboardFishLength = computed(() => _toNumOrNull(selectedDashboardFish.value?.body_length_cm))
const selectedDashboardFishNoTail = computed(() => _toNumOrNull(selectedDashboardFish.value?.body_length_no_tail_cm))
const selectedDashboardFishHeight = computed(() => _toNumOrNull(selectedDashboardFish.value?.body_height_cm))
const selectedDashboardFishWeight = computed(() => _toNumOrNull(selectedDashboardFish.value?.pred_weight_g))
const selectedDashboardFishCf = computed(() => _toNumOrNull(selectedDashboardFish.value?.condition_factor))
const selectedDashboardFishDamage = computed(() => {
  const f: any = selectedDashboardFish.value
  if (!f) return '--'
  const r = _toNumOrNull(f.surface_damage_ratio)
  if (r === null) return '--'
  return (r * 100).toFixed(2)
})

const dashboardFishImageStyle = ref<Record<string, string>>({})

function onDashboardFishImageLoad(e: Event) {
  const img = e.target as HTMLImageElement
  if (!img) return
  const box = img.parentElement as HTMLElement | null
  if (!box) return
  const iw = img.naturalWidth || 1
  const ih = img.naturalHeight || 1
  const bw = box.clientWidth || 1
  const bh = box.clientHeight || 1
  const baseScale = Math.min((bw * 0.92) / iw, (bh * 0.92) / ih)
  const areaRatioAfterBase = (iw * baseScale * ih * baseScale) / (bw * bh)
  let finalScale = baseScale
  if (areaRatioAfterBase < 0.32) {
    finalScale = Math.min(baseScale * 1.18, (bw * 0.92) / iw, (bh * 0.92) / ih)
  }
  dashboardFishImageStyle.value = {
    width: `${Math.max(1, Math.round(iw * finalScale))}px`,
    height: `${Math.max(1, Math.round(ih * finalScale))}px`,
    objectFit: 'contain',
  }
}

watch(
  () => selectedDashboardFishImageUrl.value,
  () => {
    dashboardFishImageStyle.value = {}
  }
)

function getVideoStartTimeForQuery(v: any): string | null {
  if (!v) return null
  return (
    v.started_at ||
    v.finished_at ||
    v.created_at ||
    v.updated_at ||
    null
  )
}

function statusTagText(status: string): string {
  const s = String(status || '').toLowerCase()
  if (s === 'done') return '已完成'
  if (s === 'failed') return '失败'
  if (s === 'processing' || s === 'running') return '运行中'
  if (s === 'queued') return '排队中'
  if (s === 'interrupted') return '已中断'
  if (s === 'recording') return '录制中'
  return s || '未知'
}

function sameDate(a: string | null | undefined, dateStr: string): boolean {
  if (!a || !dateStr) return false
  return String(a).slice(0, 10) === dateStr
}

function normalizeDashboardVideoOptions(items: any[]): any[] {
  if (!Array.isArray(items)) return []
  return items
    .map((v) => {
      const videoId = String(v.video_id || v.id || v.task_id || '').trim()
      if (!videoId) return null
      const status = String(v.status || v.task_status || '').trim()
      const startTime = getVideoStartTimeForQuery(v)
      const datePart = startTime ? formatChinaDateTime(startTime) : ''
      const label = `${videoId}${status ? `（${statusTagText(status)}）` : ''}${datePart ? ` ${datePart}` : ''}`
      return {
        ...v,
        video_id: videoId,
        status,
        label,
      }
    })
    .filter(Boolean) as any[]
}

async function loadDashboardFishQueryVideos() {
  const date = fishQueryDate.value
  let items: any[] = []
  // 1) /api/reports/fish-quality/videos
  try {
    const resp = await http.get('/api/reports/fish-quality/videos', {
      params: { report_date: date },
    })
    const data = resp.data?.data || resp.data || {}
    if (Array.isArray((data as any).items)) items = (data as any).items
    else if (Array.isArray((data as any).videos)) items = (data as any).videos
    else if (Array.isArray(data)) items = data as any[]
  } catch (err) {
    // 2) fallback: /api/videos 过滤日期
    try {
      const resp = await http.get('/api/videos', {
        params: { page: 1, page_size: 200 },
      })
      const data = resp.data?.data || resp.data || {}
      const list = Array.isArray((data as any).items) ? (data as any).items : []
      items = list.filter((v: any) => sameDate(getVideoStartTimeForQuery(v), date))
    } catch (e) {
      console.warn('[fish-query] load videos failed:', err, e)
      items = []
    }
  }
  let normalized = normalizeDashboardVideoOptions(items)
  // 如果当前 fishQueryVideoId 不在列表里但有值，要把它补进列表，避免下拉显示空。
  if (fishQueryVideoId.value) {
    const exists = normalized.some(
      (v) => String(v.video_id) === String(fishQueryVideoId.value),
    )
    if (!exists) {
      normalized.unshift({
        video_id: String(fishQueryVideoId.value),
        status: '',
        label: String(fishQueryVideoId.value),
      })
    }
  }
  fishQueryVideos.value = normalized
  console.log('[Dashboard FishQuery] videos:', fishQueryVideos.value.length, fishQueryVideos.value.slice(0, 3))
}

async function loadDashboardFishQueryFishes(videoId: string) {
  fishQueryFishes.value = []
  fishQueryFishId.value = ''
  dashboardFishImageStyle.value = {}
  if (!videoId) return
  try {
    const resp = await http.get(
      `/api/algorithm/videos/${encodeURIComponent(videoId)}/result`,
    )
    const payload: any = resp.data?.data || resp.data || {}
    const fish = payload.fish || payload.fishes || []
    fishQueryFishes.value = Array.isArray(fish) ? fish : []
    console.log('[Dashboard FishQuery] fishes:', fishQueryFishes.value.length, fishQueryFishes.value.slice(0, 3))
    if (fishQueryFishes.value.length > 0) {
      fishQueryFishId.value = getDashboardFishOptionValue(fishQueryFishes.value[0])
    }
  } catch (err) {
    console.warn('[fish-query] load fish failed:', err)
    fishQueryFishes.value = []
  }
}

async function onFishQueryDateChange(dateStr: string) {
  const finalDate = String(dateStr || fishQueryDate.value || '').slice(0, 10)
  if (finalDate) fishQueryDate.value = finalDate
  fishQueryVideoId.value = ''
  fishQueryFishId.value = ''
  fishQueryFishes.value = []
  await loadDashboardFishQueryVideos()
}

async function onFishQueryVideoChange(videoId: string) {
  fishQueryVideoId.value = String(videoId || '')
  fishQueryFishId.value = ''
  await loadDashboardFishQueryFishes(fishQueryVideoId.value)
}

function onFishQueryFishChange(_val: string) {
  // selectedDashboardFish computed will update automatically
}

watch(
  () => fishQueryDate.value,
  async () => {
    await loadDashboardFishQueryVideos()
    if (fishQueryVideoId.value) {
      await loadDashboardFishQueryFishes(fishQueryVideoId.value)
    }
  },
  { immediate: true },
)

watch(
  () => fishQueryVideoId.value,
  async (vid) => {
    if (vid) await loadDashboardFishQueryFishes(String(vid))
  },
)

// 算法新字段：选中鱼的字段统一走 fishFields helper（兼容旧字段）
const selectedFishImageUrl = computed(() => getFishImageUrl(selectedFish.value) || '')
const selectedFishLengthCm = computed(() => getFishLengthCm(selectedFish.value))
const selectedFishNoTailLengthCm = computed(() => getFishNoTailLengthCm(selectedFish.value))
const selectedFishHeightCm = computed(() => getFishHeightCm(selectedFish.value))
const selectedFishWeightG = computed(() => getFishWeightG(selectedFish.value))
const selectedFishConditionFactor = computed(() => getFishConditionFactor(selectedFish.value))
const selectedFishDamagePercent = computed(() => getFishDamagePercent(selectedFish.value))
const selectedFishErrorMessage = computed(() => getFishErrorMessage(selectedFish.value))
const selectedFishTrackId = computed(() => {
  const id = getFishDisplayId(selectedFish.value)
  return id === null || id === undefined ? '--' : id
})

async function handleStartCalibrationCamera() {
  if (cameraStarting.value) return
  cameraStarting.value = true
  try {
    const resp = await http.post(
      '/api/camera_yzt/start',
      {
        stream_url: cameraConfig.stream_url,
        width: cameraConfig.width,
        height: cameraConfig.height,
        fps: cameraConfig.fps,
      },
      { timeout: 30 * 1000 }
    )
    const data = resp.data || {}
    if (data.reused_existing_stream) {
      Message.success('已复用树莓派现有推流')
    } else if (data.force_restarted) {
      Message.success('摄像头已强制重启')
    } else {
      Message.success('摄像头已开启')
    }
    cameraStreamActive.value = true
    startCalibrationFramePolling()
  } catch (e: any) {
    const msg = e?.message || '开启摄像头失败'
    if (/stream already running/i.test(msg)) {
      Message.warning('树莓派摄像头状态不同步，请在摄像头设置中点击"强制重启摄像头"')
    } else {
      Message.error(msg)
    }
  } finally {
    cameraStarting.value = false
  }
}

async function handleStopCalibrationCamera() {
  try {
    await http.post('/api/camera_yzt/stop')
  } catch (e: any) {
    console.warn('[calibration] stop camera failed:', e?.message)
  }
  cameraStreamActive.value = false
  stopCalibrationFramePolling()
  // 停止后刷新状态，便于显示 pi_status_after / desync 变化
  try {
    pollCameraStatus()
  } catch {}
}

function startCalibrationFramePolling() {
  stopCalibrationFramePolling()
  calibrationFrameTs.value = Date.now()
  calibrationFrameTimer = window.setInterval(() => {
    calibrationFrameTs.value = Date.now()
  }, 1000)
}

function stopCalibrationFramePolling() {
  if (calibrationFrameTimer) {
    clearInterval(calibrationFrameTimer)
    calibrationFrameTimer = null
  }
}

function apiDomainForFrame(): string {
  // /api/camera_yzt/frame 是后端路由，但若 VITE_API_DOMAIN 设置时需要拼装绝对 URL。
  const base: string =
    (import.meta.env.VITE_API_BASE_URL as string) ||
    (import.meta.env.VITE_API_DOMAIN as string) ||
    ''
  return base.replace(/\/$/, '')
}

// 标定抓拍错误 → 用户友好文案映射（隐藏 RTMP / IP / MediaMTX / fish_camera 等技术细节）
function isClientTimeoutError(err: any): boolean {
  if (!err) return false
  if (err?.code === 'ECONNABORTED' || err?.code === 'ECONNRESET') return true
  const msg = String(err?.message || '')
  return /timeout of \d+ms exceeded/i.test(msg) || /\b15000ms\b/.test(msg)
}

function friendlyCalibrationError(
  errCode?: string,
  fallback?: string | null | undefined,
): string {
  const fb = (fallback || '').trim()
  switch (errCode) {
    case 'CAMERA_DEVICE_NOT_CONFIGURED':
      // 主流程已经不再依赖 FISH_PI_API_BASE 等环境变量；这条分支仅作为兜底。
      return '摄像头连接失败，请检查设备电源和网络连接。'
    case 'CAMERA_DEVICE_START_FAILED':
      return '摄像头连接失败，请检查设备电源和网络连接。'
    case 'CAMERA_STREAM_TIMEOUT':
    case 'FISH_CAMERA_STREAM_NOT_READY':
      return '摄像头画面获取超时，请稍后重试。'
    case 'FFMPEG_NOT_FOUND':
      return '服务器未安装 ffmpeg，无法拍摄标定帧，请联系管理员。'
    case 'CALIBRATION_SAVE_FAILED':
      return '标定帧保存失败，请稍后重试。'
    case 'CALIBRATION_CAPTURE_FAILED':
      return '摄像头画面获取失败，请稍后重试。'
    default:
      return fb || '标定失败，请检查摄像头画面是否正常。'
  }
}

/**
 * 主流程：拍摄当前摄像头帧 + 调用 adjust_calibration CLI。
 *
 * 设计变更：不再要求用户先"开启摄像头"——后端会自动通知树莓派启动推流，
 * 然后 ffmpeg 抓帧，最后执行算法。失败时只显示用户友好提示。
 */
async function handleCaptureAndCalibrate() {
  calibrationLoading.value = true
  try {
    // 把当前"摄像头输入"按钮的 cameraConfig 透传给后端，
    // 后端会复用 camera_yzt 链路自动启动 / 抓拍 / 停止。
    const resp = await captureAndRunCalibration({
      camera_id: 'default',
      device: 'cuda',
      overwrite: true,
      save_only: false,
      stream_url: cameraConfig.stream_url,
      width: cameraConfig.width,
      height: cameraConfig.height,
      fps: cameraConfig.fps,
      stop_after_capture: true,
    })
    const data = resp.data || {}
    calibrationLastResult.value = data

    // HTTP 200 但 ok=false 也走这里：提取 detail.error_code / detail.error_message。
    const detail =
      data?.detail && typeof data.detail === 'object' ? data.detail : null
    const errCode = data?.error_code || (detail && detail.error_code)
    const errMsg = data?.error_message || (detail && detail.error_message)

    const errCodeStr = (errCode || '') as string
    if (data.calibrated) {
      Message.success(data.message || '标定完成')
    } else if (data.saved) {
      // 抓帧成功但算法未标定
      Message.error(
        friendlyCalibrationError(errCodeStr, errMsg || undefined),
      )
    } else if (errCode || errMsg) {
      Message.error(
        friendlyCalibrationError(errCodeStr, errMsg || undefined),
      )
    } else {
      Message.error('标定失败，请检查摄像头画面是否正常。')
    }

    await loadCalibrationStatus()
    await loadCalibrationJsonPreview()

    // 后端标定拍摄完成后会自动 stop_stream，把前端状态也同步成已停止，
    // 这样 "关闭摄像头" 按钮、摄像头画面会立刻消失。
    // 注意：polling 已经在 1286 行的 onUnmounted 被 clearTimeout，无需手动清。
    cameraStreamActive.value = false
    cameraConnected.value = false
    cameraState.value = 'idle'
  } catch (e: any) {
    // 把 axios 超时类错误翻译成用户友好提示，不显示 AxiosError / 15000ms 等原文
    if (isClientTimeoutError(e)) {
      console.warn('[Dashboard] calibration capture timeout:', e)
      Message.error(
        '摄像头连接或标定耗时较长，请稍后重试；如多次失败，请检查设备网络和摄像头画面。',
      )
      return
    }
    const detail =
      e?.response?.data?.detail && typeof e.response.data.detail === 'object'
        ? e.response.data.detail
        : null
    const errCode =
      e?.response?.data?.error_code ||
      (detail && detail.error_code)
    const errCodeStr = (errCode || '') as string
    const errMsg =
      e?.response?.data?.error_message ||
      e?.response?.data?.detail?.error_message ||
      e?.message
    // debug logging only：不在 UI 上暴露技术细节
    if (detail && detail.debug_message) {
      console.warn(
        '[Dashboard] calibration capture debug:',
        detail.debug_message
      )
    }
    Message.error(friendlyCalibrationError(errCodeStr, errMsg))
  } finally {
    calibrationLoading.value = false
  }
}

/**
 * 备用方式：使用服务器已有标定图片路径。
 */
async function handleRunCalibrationManual() {
  await runCalibrationWithImage(calibrationImagePath.value)
  await loadCalibrationJsonPreview()
}

async function loadCalibrationJsonPreview() {
  try {
    if (!calibrationStatus.value?.calibration_json) {
      calibrationRawJson.value = ''
      return
    }
    const path = calibrationStatus.value.calibration_json as string
    const url = '/static/' + path.replace(/^data\//, '').replace(/^\/+/, '')
    const resp = await http.get(url, { responseType: 'text' })
    const txt = typeof resp.data === 'string' ? resp.data : JSON.stringify(resp.data, null, 2)
    calibrationRawJson.value =
      txt.length > 4096 ? txt.slice(0, 4096) + '\n... (truncated)' : txt
  } catch (e: any) {
    calibrationRawJson.value = '(读取失败：' + (e?.message || String(e)) + ')'
  }
}

let taskPollTimer: number | null = null
let cameraPollTimer: number | null = null
let durationTimer: number | null = null
let pollAbort = false

// localStorage 键
const LS_KEY_VIDEO_ID = 'dashboard_current_video_id'
const LS_KEY_SOURCE = 'dashboard_current_source_type'
const LS_KEY_SAVED_AT = 'dashboard_current_saved_at'

// ---- 时间范围选择 ----
type RangeKey = 'today' | 'last_7d' | 'last_30d' | 'all' | 'custom'
const RANGE_OPTIONS: { key: RangeKey; label: string }[] = [
  { key: 'today', label: '今天' },
  { key: 'last_7d', label: '最近7天' },
  { key: 'last_30d', label: '最近1个月' },
  { key: 'all', label: '全部' },
  { key: 'custom', label: '自定义' },
]

// 定时录制快捷选项（分钟）—— 按场景分组
// 选中的判断仍走 selectedDurationMinutes === opt.minutes，不依赖 cap 字段。
const DURATION_GROUP_TEST: { minutes: number; label: string; cap?: boolean }[] = [
  { minutes: 1, label: '1 分钟（测试）' },
]
const DURATION_GROUP_COMMON: { minutes: number; label: string; cap?: boolean }[] = [
  { minutes: 30, label: '30 分钟' },
  { minutes: 60, label: '1 小时' },
  { minutes: 120, label: '2 小时' },
]
const DURATION_GROUP_LONG: { minutes: number; label: string; cap?: boolean }[] = [
  { minutes: 240, label: '4 小时' },
  { minutes: 480, label: '8 小时' },
  { minutes: 1440, label: '24 小时', cap: true },
]
const selectedRangeType = ref<RangeKey>('today')
const customRange = ref<string[]>([])
const rangeHint = ref<string>('')

  // 获取图片内点坐标，发送选鱼请求，更新 selectedFish
async function handleCameraFrameClick(event: MouseEvent) {
  if (!cameraStreamActive.value || !currentVideoId.value) {
    selectedFish.value = null
    selectedFishMessage.value = '当前没有正在检测的视频'
    return
  }

  const img = event.currentTarget as HTMLImageElement
  const point = getContainImagePoint(event, img)

  if (!point) {
    return
  }

  const fps = cameraConfig.fps || 25
  const baseTime = startedAtMs ?? Date.now()
  const elapsedSeconds = Math.max(0, (Date.now() - baseTime) / 1000)
  const frameIndex = Math.floor(elapsedSeconds * fps)

  // 第一阶段：/api/dashboard/frame/select-fish 接口已删除。
  // 真实算法结果请从鱼列表（fish[]）按 track_id 选择。
  // 这里保留点击坐标 / frame_index 是为了前端后续扩展；
  // 当前直接把消息提示出来，selectedFish 交给专门的鱼列表入口。
  selectedFish.value = null
  selectedFishMessage.value = '请从右侧鱼列表选择一条鱼（点击帧选鱼接口已废弃）'
  return

  // 旧实现（保留注释，后续如果后端重新提供选鱼接口可以恢复）：
  // const resp = await Promise.reject(new Error('legacy selectFishFromFrame disabled'))
  // ...
}

function computeRange(): { start: string; end: string } | null {
  const now = dayjs()
  if (selectedRangeType.value === 'today') {
    return { start: now.startOf('day').toISOString(), end: now.toISOString() }
  }
  if (selectedRangeType.value === 'last_7d') {
    return { start: now.subtract(7, 'day').toISOString(), end: now.toISOString() }
  }
  if (selectedRangeType.value === 'last_30d') {
    return { start: now.subtract(30, 'day').toISOString(), end: now.toISOString() }
  }
  if (selectedRangeType.value === 'all') {
    // 全部：以 2000-01-01 作为下界
    return { start: '2000-01-01T00:00:00.000Z', end: now.toISOString() }
  }
  if (selectedRangeType.value === 'custom' && customRange.value.length === 2) {
    return { start: dayjs(customRange.value[0]).toISOString(), end: dayjs(customRange.value[1]).toISOString() }
  }
  return null
}

function updateRangeHint() {
  const r = computeRange()
  if (!r) { rangeHint.value = '请选择时间范围'; return }
  rangeHint.value = `${dayjs(r.start).format('MM-DD HH:mm')} ~ ${dayjs(r.end).format('MM-DD HH:mm')}`
}

// ---- 进度文案与多阶段进度 ----
const progressText = computed(() => {
  const s = currentVideoStatus.value
  if (cameraStreamActive.value && !currentVideoId.value) return '正在连接'
  if (s === 'queued') return '排队中'
  if (s === 'processing') {
    const stage = currentAlgorithm.value
    if (stage === 'extract_frames') return '正在抽帧'
    if (stage === 'crop_fish') return '正在裁剪鱼图'
    if (stage === 'correct_image') return '正在矫正图像'
    if (stage === 'five_dimension') {
      const total = Math.max(totalFish.value, processedFish.value, 1)
      return `正在分析鱼体 ${processedFish.value}/${total}`
    }
    return '正在处理'
  }
  if (s === 'done') return '检测完成'
  if (s === 'no_fish_detected') return '未检测到鱼'
  if (s === 'failed') return '检测失败'
  if (s === 'stopped') return '已停止'
  if (cameraStreamActive.value) return '运行中'
  return '等待上传'
})

// 多阶段进度计算公式（任务从 0 跳 100 太快的修复）：
//   - queued                    -> 5
//   - processing + extract_frames -> 20
//   - processing + crop_fish    -> 30
//   - processing + correct/five -> 60 + 35 * processed/total
//   - done / no_fish_detected   -> 100
//   - failed / stopped          -> 0
//   - 其它（无任务）             -> 0
function computeProgress(): number {
  const s = currentVideoStatus.value
  const total = totalFish.value
  const done = processedFish.value
  if (s === 'queued') return 5
  if (s === 'processing') {
    const stage = currentAlgorithm.value
    if (stage === 'extract_frames') return 20
    if (stage === 'crop_fish') return 30
    if (stage === 'correct_image' || stage === 'five_dimension') {
      if (total > 0) {
        return Math.min(95, Math.round(60 + 35 * done / total))
      }
      return 60
    }
    return 30
  }
  if (s === 'done' || s === 'no_fish_detected') return 100
  if (s === 'failed' || s === 'stopped') return 0
  return 0
}

const canStart = computed(() => {
  if (cameraStreamActive.value) return false
  return cameraState.value === 'idle' || cameraState.value === 'error'
})
const canStop = computed(() => cameraState.value === 'running')
const cameraButtonText = computed(() => {
  if (cameraState.value === 'starting') return '正在连接'
  if (cameraState.value === 'stopping') return '正在关闭'
  if (cameraStreamActive.value || cameraState.value === 'running') return '关闭摄像头'
  return '摄像头输入'
})

const cameraButtonIcon = computed(() => {
  if (cameraStreamActive.value || cameraState.value === 'running' || cameraState.value === 'starting') {
    return 'fa-solid fa-stop'
  }
  return 'fa-solid fa-camera'
})
// ---- 辅助 ----
function formatDuration(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000))
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

function formatSeconds(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '--'
  const s = Math.max(0, Math.floor(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const ss = s % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(ss).padStart(2, '0')}`
}

// 把后端 /api/camera_yzt/start、/stop、/status 返回的统一字段
// 同步到前端响应式状态，作为倒计时卡片 / 停止原因卡片的唯一数据源。
function applyCameraTimingFromStatus(data: any) {
  if (!data) return
  const info = data.timed_recording_info || {}

  const nextRecordDuration =
    data.record_duration_seconds ?? info.record_duration_seconds ?? null
  recordDurationSeconds.value =
    typeof nextRecordDuration === 'number' ? nextRecordDuration : null

  const nextScheduledStop =
    data.scheduled_stop_at ?? info.scheduled_stop_at ?? null
  scheduledStopAt.value = nextScheduledStop || null

  const nextRemaining =
    data.remaining_seconds ?? info.remaining_seconds ?? null
  remainingSeconds.value =
    typeof nextRemaining === 'number' ? nextRemaining : null

  timedRecording.value = Boolean(
    data.timed_recording ?? info.timed_recording ?? false,
  )
  timerPending.value = Boolean(
    data.timer_pending ?? info.timer_pending ?? false,
  )
  autoStopTriggered.value = Boolean(
    data.auto_stop_triggered ?? info.auto_stop_triggered ?? false,
  )
  stopReason.value = (data.stop_reason ?? info.stop_reason ?? '') || ''
  stopSource.value = (data.stop_source ?? info.stop_source ?? '') || ''

  // 当后端明确 idle 且非 timed_recording 时，清理倒计时字段（保留 stop_reason/source 用于显示已停止原因）
  const state = data.state || null
  if ((state === 'idle' || state === 'stopped' || state === 'error') && !timedRecording.value) {
    if (!autoStopTriggered.value && !stopReason.value) {
      scheduledStopAt.value = null
    }
  }
}

// 本地 1 秒递减，仅做 UI 平滑；每次 /status 成功时会被后端值覆盖。
// 关键：timer_pending=true 时不能递减（armed 时间还没到，倒计时未开始）。
function tickRemainingSeconds() {
  if (!timedRecording.value) return
  if (timerPending.value) return
  if (remainingSeconds.value === null || remainingSeconds.value === undefined) return
  remainingSeconds.value = Math.max(0, remainingSeconds.value - 1)
  if (remainingSeconds.value <= 0) {
    stopRemainingTick()
  }
}

function startRemainingTick() {
  if (remainingTickTimer) return
  remainingTickTimer = window.setInterval(tickRemainingSeconds, 1000)
}

function stopRemainingTick() {
  if (remainingTickTimer) {
    clearInterval(remainingTickTimer)
    remainingTickTimer = null
  }
}

// 快捷选项：1 / 30 / 60 / 120 / 240 / 480 / 1440 分钟
function pickPresetDuration(minutes: number) {
  selectedDurationMinutes.value = minutes
  customDurationHours.value = Math.floor(minutes / 60)
  customDurationMinutes.value = minutes % 60
}

async function confirmStartTimedRecording() {
  const duration = calcSelectedDurationMinutes()
  if (!Number.isFinite(duration) || duration <= 0) {
    Message.error('录制时长必须大于 0 分钟')
    return
  }
  if (duration > 1440) {
    Message.error('录制时长不能超过 24 小时')
    return
  }
  timedRecordingDialogVisible.value = false
  await startCameraCapture(duration)
}

// 解释 视频状态、算法状态、帧状态 等等各种状态字段，返回用户友好的文案
function fmt(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '--'
  return Number(v).toFixed(digits)
}

// 获取鼠标点击位置在图片中的坐标（考虑图片适应容器时的缩放和偏移），返回相对于原图的坐标（naturalWidth/naturalHeight），如果点击位置不在图片上则返回 null
function getContainImagePoint(event: MouseEvent, img: HTMLImageElement) {
  const rect = img.getBoundingClientRect()

  const naturalWidth = img.naturalWidth || cameraConfig.width
  const naturalHeight = img.naturalHeight || cameraConfig.height

  const boxWidth = rect.width
  const boxHeight = rect.height

  const imageRatio = naturalWidth / naturalHeight
  const boxRatio = boxWidth / boxHeight

  let drawWidth = boxWidth
  let drawHeight = boxHeight
  let offsetX = 0
  let offsetY = 0

  if (imageRatio > boxRatio) {
    drawWidth = boxWidth
    drawHeight = boxWidth / imageRatio
    offsetY = (boxHeight - drawHeight) / 2
  } else {
    drawHeight = boxHeight
    drawWidth = boxHeight * imageRatio
    offsetX = (boxWidth - drawWidth) / 2
  }

  const clickX = event.clientX - rect.left - offsetX
  const clickY = event.clientY - rect.top - offsetY

  if (clickX < 0 || clickY < 0 || clickX > drawWidth || clickY > drawHeight) {
    return null
  }

  return {
    x: clickX * naturalWidth / drawWidth,
    y: clickY * naturalHeight / drawHeight,
  }
}

function getDamageClass(damage?: string) {
  if (!damage || damage === '无' || damage === '无明显破损') return 'none'
  if (damage === '轻微') return 'light'
  return 'severe'
}

function clearAllTimers() {
  if (durationTimer) { clearInterval(durationTimer); durationTimer = null }
  if (taskPollTimer) { clearTimeout(taskPollTimer); taskPollTimer = null }
  if (cameraPollTimer) { clearTimeout(cameraPollTimer); cameraPollTimer = null }
  if (remainingTickTimer) { clearInterval(remainingTickTimer); remainingTickTimer = null }
  if (apertureFrameTimer) { clearInterval(apertureFrameTimer); apertureFrameTimer = null }
}

// 检测耗时 = finished_at - started_at（带状态刷新）。
// 运行中：每秒刷新一次（基于真实时间戳，**与播放暂停无关**）。
function startDurationCounter() {
  if (durationTimer) return
  durationTimer = window.setInterval(() => {
    recomputeDurationFromTimestamps()
  }, 1000)
}

function stopDurationCounter() {
  if (durationTimer) { clearInterval(durationTimer); durationTimer = null }
}

let startedAtMs: number | null = null
let finishedAtMs: number | null = null

function recomputeDurationFromTimestamps() {
  if (startedAtMs === null) {
    duration.value = '00:00:00'
    return
  }
  const end = finishedAtMs ?? Date.now()
  duration.value = formatDuration(end - startedAtMs)
}

function applyTimestampsFromTask(task: any) {
  // 优先用 video_task.started_at，没有就退到 created_at
  if (task?.started_at) {
    startedAtMs = new Date(task.started_at).getTime()
  } else if (task?.created_at) {
    startedAtMs = new Date(task.created_at).getTime()
  } else {
    startedAtMs = null
  }
  finishedAtMs = task?.finished_at ? new Date(task.finished_at).getTime() : null
  startTime.value = startedAtMs ? formatChinaDateTime(startedAtMs) : null
  endTime.value = finishedAtMs ? formatChinaDateTime(finishedAtMs) : null
  if (startedAtMs !== null) {
    if (finishedAtMs === null) startDurationCounter()
    else stopDurationCounter()
    recomputeDurationFromTimestamps()
  } else {
    duration.value = '00:00:00'
    stopDurationCounter()
  }
}

// 持久化当前任务到 localStorage
function saveCurrentTaskToStorage(videoId: string, sourceType: string) {
  try {
    localStorage.setItem(LS_KEY_VIDEO_ID, videoId)
    localStorage.setItem(LS_KEY_SOURCE, sourceType)
    localStorage.setItem(LS_KEY_SAVED_AT, Date.now().toString())
  } catch (e) { /* ignore */ }
}

function clearCurrentTaskFromStorage() {
  try {
    localStorage.removeItem(LS_KEY_VIDEO_ID)
    localStorage.removeItem(LS_KEY_SOURCE)
    localStorage.removeItem(LS_KEY_SAVED_AT)
  } catch (e) { /* ignore */ }
}

function readCurrentTaskFromStorage(): { videoId: string; sourceType: string } | null {
  try {
    const videoId = localStorage.getItem(LS_KEY_VIDEO_ID)
    if (!videoId) return null
    const sourceType = localStorage.getItem(LS_KEY_SOURCE) || 'upload'
    return { videoId, sourceType }
  } catch {
    return null
  }
}

// 应用 video_task 数据到 UI（**绝不允许使用全局 /api/fishes 的 total**）
function applyVideoTaskToUi(task: any) {
  if (!task) return
  currentVideoId.value = task.video_id
  currentVideoStatus.value = task.status || ''
  currentAlgorithm.value = task.current_algorithm || ''
  lastError.value = task.last_error || null

  // 关键：鱼数量优先用 fish_count_from_db（来自 SELECT COUNT(*)）
  const dbCount = typeof task.fish_count_from_db === 'number'
    ? task.fish_count_from_db
    : null
  totalFish.value = dbCount !== null ? dbCount : (task.total_fish || 0)
  processedFish.value = task.processed_fish || 0

  // 阶段式进度
  progress.value = computeProgress()

  // 时间字段
  applyTimestampsFromTask(task)

  // 视频文件时长
  if (typeof task.video_duration_seconds === 'number' && task.video_duration_seconds > 0) {
    videoDurationSeconds.value = task.video_duration_seconds
    videoDurationLabel.value = formatSeconds(task.video_duration_seconds)
  } else {
    videoDurationSeconds.value = null
    videoDurationLabel.value = '--'
  }
}

async function pollVideoTask(videoId: string) {
  pollAbort = false
  const check = async () => {
    if (pollAbort || currentVideoId.value !== videoId) return
    try {
      const resp = await http.get(`/api/videos/${videoId}`)
      const data = resp.data
      applyVideoTaskToUi(data)

      // 终止态：停止 duration counter，保留最终结果
      if (data.status === 'done' || data.status === 'no_fish_detected' || data.status === 'failed' || data.status === 'stopped') {
        if (data.status === 'done') {
          progress.value = 100
        }
        if (data.finished_at) {
          endTime.value = formatChinaDateTime(data.finished_at)
        }
        stopDurationCounter()
        return
      }
      taskPollTimer = window.setTimeout(check, 2000)
    } catch (e) {
      console.error('轮询任务状态失败:', e)
      taskPollTimer = window.setTimeout(check, 3000)
    }
  }
  check()
}

function handleUpload() {
  logUserAction({ module: '算法检测', action: 'upload_video', message: '点击上传视频' })
  if (!canStart.value) return
  videoWallRef.value?.triggerFileInput()
}

function handleUploadSuccess(data: { video_id: string, videoSrc: string }) {
  logUserAction({ module: '算法检测', action: 'start_algorithm_detection', targetType: 'video', targetId: data.video_id, message: '上传视频并开始算法检测' })
  currentVideoId.value = data.video_id
  currentSourceType.value = 'upload'
  saveCurrentTaskToStorage(data.video_id, 'upload')
  // 立即拉一次 task 拿到 started_at 等
  pollVideoTask(data.video_id)
}

function handlePlay() {
  if (currentVideoId.value) videoWallRef.value?.playVideo()
}

function handlePause() {
  if (currentVideoId.value) videoWallRef.value?.pauseVideo()
}

function handleReDetect() {
  if (canStart.value) handleUpload()
}

function handleFishSelected(fish: FishDetail) {
  selectedFish.value = fish
}

function handleClearCurrentTask() {
  pollAbort = true
  clearAllTimers()
  clearCurrentTaskFromStorage()
  currentVideoId.value = ''
  currentSourceType.value = ''
  currentVideoStatus.value = ''
  currentAlgorithm.value = ''
  totalFish.value = 0
  processedFish.value = 0
  progress.value = 0
  startTime.value = null
  endTime.value = null
  duration.value = '00:00:00'
  videoDurationLabel.value = '--'
  videoDurationSeconds.value = null
  selectedFish.value = null
  lastError.value = null
  startedAtMs = null
  finishedAtMs = null
}

function onCorrectedImgError(e: Event) {
  const img = e.target as HTMLImageElement
  img.style.opacity = '0.3'
}

function onOriginalImgError(e: Event) {
  const img = e.target as HTMLImageElement
  img.style.opacity = '0.3'
}

// ---------- 摄像头 ----------
async function pollCameraStatus() {
  const check = async () => {
    if (pollAbort || !cameraStreamActive.value) return
    try {
      const statusResp = await http.get('/api/camera_yzt/status')
      const statusData = statusResp.data
      cameraState.value = statusData.state || 'idle'

      // 解释：摄像头状态不活跃时，可能是正在连接（starting），也可能是已停止（idle/error）。只有在 idle/error 状态才真正认为摄像头不活跃并关闭画面，starting 状态则继续等待。
      if (!statusData.is_active) {
        cameraConnected.value = false

        // 正在连接时，后端可能还没有进入 active，不能立刻把画面关掉
        if (cameraState.value === 'starting') {
          cameraStreamActive.value = true
          cameraPollTimer = window.setTimeout(check, 2000)
          return
        }

        if (cameraState.value === 'idle' || cameraState.value === 'error') {
          cameraStreamActive.value = false
          stopCameraFramePolling()

          if (currentVideoId.value) {
            try {
              const r = await http.get(`/api/videos/${currentVideoId.value}`)
              const v = r.data
              applyVideoTaskToUi(v)
              if (v.finished_at) {
                endTime.value = formatChinaDateTime(v.finished_at)
              }
            } catch {}
          } else {
            endTime.value = formatChinaDateTime(Date.now())
          }

          stopDurationCounter()
          return
        }

        cameraPollTimer = window.setTimeout(check, 2000)
        return
      }

      cameraConnected.value = true
      cameraStreamActive.value = true

      // 同步定时录制字段；后端是权威值
      applyCameraTimingFromStatus(statusData)
      if (timedRecording.value) {
        startRemainingTick()
      } else {
        stopRemainingTick()
      }

      if (statusData.video_id && !currentVideoId.value) {
        currentVideoId.value = statusData.video_id
        currentSourceType.value = 'rtsp'
        saveCurrentTaskToStorage(statusData.video_id, 'rtsp')
      }
      if (statusData.fish_detected !== undefined && statusData.fish_detected !== null) {
        // 摄像头运行时短暂用 fish_detected
        if (totalFish.value === 0) totalFish.value = statusData.fish_detected
      }
      if (currentVideoId.value) {
        try {
          const r = await http.get(`/api/videos/${currentVideoId.value}`)
          const v = r.data
          applyVideoTaskToUi(v)
        } catch {}
      }
      cameraPollTimer = window.setTimeout(check, 2000)
    } catch (e) {
      console.error('轮询摄像头状态失败:', e)
      cameraPollTimer = window.setTimeout(check, 3000)
    }
  }
  check()
}

async function startCameraCapture(durationMinutes?: number) {
  if (cameraStarting.value) return
  cameraStarting.value = true
  logUserAction({ module: '摄像头控制', action: 'start_camera', message: '点击启动摄像头' })
  cameraStreamActive.value = true
  cameraConnected.value = false
  cameraState.value = 'starting'

  // 摄像头启动后立刻开始计算耗时
  startedAtMs = Date.now()
  finishedAtMs = null
  startTime.value = formatChinaDateTime(startedAtMs)
  endTime.value = null
  duration.value = '00:00:00'
  startDurationCounter()

  const payload: Record<string, any> = {
    stream_url: cameraConfig.stream_url,
    width: cameraConfig.width,
    height: cameraConfig.height,
    fps: cameraConfig.fps,
  }
  // 只有主"摄像头输入"开始录制才带 duration_minutes；
  // 标定等流程不传 duration_minutes，避免影响既有标定行为。
  if (typeof durationMinutes === 'number' && Number.isFinite(durationMinutes) && durationMinutes > 0) {
    payload.duration_minutes = durationMinutes
  }

  try {
    const resp = await http.post('/api/camera_yzt/start', payload)

    const data = resp.data

    if (data.status === 'error') {
      cameraState.value = 'error'
      cameraConnected.value = false
      cameraStreamActive.value = false
      stopCameraFramePolling()
      endTime.value = formatChinaDateTime(Date.now())
      finishedAtMs = Date.now()
      stopDurationCounter()
      stopRemainingTick()
      // 失败时把后端 status 返回的 timing 字段也同步下来，便于 UI 显示错误后状态
      applyCameraTimingFromStatus(data)
      const errMsg = (data && (data.message || data.error)) || '启动摄像头失败'
      Message.error(errMsg)
      return
    }

    cameraStreamActive.value = true
    cameraState.value = data.state || 'starting'

    // 兜底：如果在弹定时录制弹窗时还没有 result mode，但用户在 confirmStartTimedRecording
    // 之前手动从 /admin/video-history 跳转进来，路由 query 会保留 mode=result&video_id=xxx，
    // route watch 也会触发 loadResultMode。这里在 start 成功时再清理一次 result 状态。
    if (isResultMode.value) {
      switchToCameraLiveMode()
    }

    if (data.video_id) {
      currentVideoId.value = data.video_id
      currentSourceType.value = 'rtsp'
      saveCurrentTaskToStorage(data.video_id, 'rtsp')
    }

    // 提示：复用现推流 / 强制重启 / 普通启动
    if (data.reused_existing_stream) {
      Message.success('已复用树莓派现有推流')
    } else if (data.force_restarted) {
      Message.success('摄像头已强制重启')
    }

    // 同步定时录制字段；后端是权威值
    applyCameraTimingFromStatus(data)
    if (timedRecording.value) {
      startRemainingTick()
    } else {
      stopRemainingTick()
    }

    // 注意：必须在 start 请求成功之后，再开始拉帧和轮询状态
    startCameraFramePolling()
    pollCameraStatus()
  } catch (e: any) {
    console.error('启动摄像头失败:', e)
    cameraState.value = 'error'
    cameraConnected.value = false
    cameraStreamActive.value = false
    stopCameraFramePolling()
    endTime.value = formatChinaDateTime(Date.now())
    finishedAtMs = Date.now()
    stopDurationCounter()
    stopRemainingTick()
    const msg = e?.message || String(e)
    if (/stream already running/i.test(msg)) {
      Message.warning(
        '树莓派摄像头状态不同步，请在摄像头设置中点击"强制重启摄像头"',
      )
    } else {
      Message.error(msg || '启动摄像头失败')
    }
  } finally {
    cameraStarting.value = false
  }
}

async function stopCameraCapture() {
  logUserAction({ module: '摄像头控制', action: 'stop_camera', message: '点击停止摄像头' })
  cameraState.value = 'stopping'
  stopCameraFramePolling()
  stopRemainingTick()
  try {
    const resp = await http.post('/api/camera_yzt/stop', {
      reason: 'dashboard_camera_button',
      source: 'Dashboard.vue',
    })
    const data = resp.data
    if (typeof data.fish_detected === 'number') {
      totalFish.value = data.fish_detected
    }

    // 本阶段新增：处理后端入队结果，触发任务状态轮询
    if (data.video_id) {
      currentVideoId.value = data.video_id
      currentSourceType.value = 'rtsp'
      saveCurrentTaskToStorage(data.video_id, 'rtsp')
    }

    if (data.queued && data.video_id) {
      Message.success(
        `录制已停止，算法已入队 (job_id=${data.job_id || 'n/a'})`,
      )
      // 触发任务状态轮询
      pollVideoTask(data.video_id)
    } else if (data.video_id && !data.queued) {
      const reason = data.queue_error || data.queue_message || 'video missing or empty'
      Message.warning(`录制已停止，但算法未入队：${reason}`)
    }
  } catch (e) {
    console.error('停止摄像头失败:', e)
  } finally {
    cameraStreamActive.value = false
    cameraConnected.value = false
    cameraState.value = 'idle'
    endTime.value = formatChinaDateTime(Date.now())
    finishedAtMs = Date.now()
    stopDurationCounter()
    // 兜底：手动停止的 stop_reason / stop_source 即使后端没返回，前端也展示。
    stopReason.value = stopReason.value || 'dashboard_camera_button'
    stopSource.value = stopSource.value || 'Dashboard.vue'
    autoStopTriggered.value = false
    timedRecording.value = false
    remainingSeconds.value = null
    scheduledStopAt.value = null
    // 停止后刷新 status，状态不同步提示也会更新
    try {
      pollCameraStatus()
    } catch {}
  }
}

function handleCameraInput() {
  if (
    cameraStreamActive.value ||
    cameraState.value === 'starting' ||
    cameraState.value === 'running'
  ) {
    stopCameraCapture()
    return
  }

  if (canStart.value) {
    // 主"摄像头输入"：必须先选择定时录制时长，再调用 start。
    // 关键：如果当前处于 result mode（?mode=result&video_id=xxx），
    // 必须先退出 result 展示状态，否则：
    //   1. 中央仍会显示旧视频的 grounded_sam 视频
    //   2. 右侧仍会显示旧视频的鱼指标
    //   3. route watcher 会反复把旧 result 拉回来
    // 默认值：1 小时，方便快速测试。
    if (isResultMode.value) {
      switchToCameraLiveMode()
    }
    pickPresetDuration(selectedDurationMinutes.value || 60)
    timedRecordingDialogVisible.value = true
    return
  }

  console.warn(`[Camera] 当前状态 ${cameraState.value} 不可操作`)
}

function startCameraFramePolling() {
  stopCameraFramePolling()
  const base = import.meta.env.VITE_API_DOMAIN
  cameraFrameUrl.value = `${base}/api/camera_yzt/frame?t=${Date.now()}`
  cameraFrameTimer = window.setInterval(() => {
    cameraFrameUrl.value = `${base}/api/camera_yzt/frame?t=${Date.now()}`
  }, 200)
}

function stopCameraFramePolling() {
  if (cameraFrameTimer) {
    clearInterval(cameraFrameTimer)
    cameraFrameTimer = null
  }
}

function onCameraFrameError() {
  // 单帧加载失败（可能是 204 / 流还没到 / 一帧丢失）不触发 stop。
  // 由 pollCameraStatus 决定是否真正结束。
  // eslint-disable-next-line no-console
  console.warn('[camera_frame] 单帧加载失败，继续等待下一帧 / status 轮询')
}

function handleCameraSettingsApplied(cfg: any) {
  if (!cfg) return
  if (cfg.stream_url) cameraConfig.stream_url = cfg.stream_url
  if (cfg.width) cameraConfig.width = Number(cfg.width)
  if (cfg.height) cameraConfig.height = Number(cfg.height)
  if (cfg.fps) cameraConfig.fps = Number(cfg.fps)
}

// 光圈设置入口。
//
// 设计要求（阶段 2.1 修复后）：
//   - 录制前 / 录制中 / 录制后 都能用。
//   - 录制中（status.is_active=true）→ 复用主摄像头推流，**不**调 preview/start。
//   - 录制前 / 录制后（idle）→ 调 preview/start 启动临时预览。
//   - status 拉取失败 → 仍打开弹窗，可继续尝试启动 preview。
async function handleOpenApertureSettings() {
  // 0. 弹窗总是先打开，让用户能看到界面 / 提示 / 错误状态
  apertureDialogVisible.value = true
  aperturePreviewError.value = ''
  aperturePreviewMode.value = 'unknown'

  // 1. 拉一次 status 判断
  let statusData: any = null
  try {
    const resp = await http.get('/api/camera_yzt/status')
    statusData = resp.data || {}
  } catch (e) {
    // 拉 status 失败不阻塞：仍允许用户继续尝试启动 preview
    console.warn('[aperture] fetch camera status failed:', e)
  }

  // 2. 优先复用现有推流（录制中 / 预览残留）
  if (statusData) {
    const isActive = Boolean(statusData.is_active)
    const state = String(statusData.state || 'idle')
    if (
      isActive &&
      (state === 'starting' || state === 'running' || state === 'stopping')
    ) {
      // 已经有画面：复用，不调 preview/start
      aperturePreviewActive.value = true
      aperturePreviewStartedByDialog.value = false
      aperturePreviewMode.value = 'recording-reuse'
      startApertureFramePolling()
      // 顺便 ping 一下光圈服务；失败不阻塞
      handleApertureLedPing()
      return
    }
  }

  // 3. idle / error：调 preview/start 启动临时预览
  aperturePreviewMode.value = 'preview'
  await startAperturePreview()
}

async function startAperturePreview() {
  aperturePreviewLoading.value = true
  aperturePreviewError.value = ''
  try {
    const resp = await http.post('/api/camera_yzt/preview/start', {
      stream_url: cameraConfig.stream_url || undefined,
      width: cameraConfig.width || 1920,
      height: cameraConfig.height || 1080,
      fps: cameraConfig.fps || 25,
    })
    const data = resp.data || {}
    if (data.status === 'error') {
      aperturePreviewError.value =
        data.message || data.error || '摄像头预览启动失败'
      Message.error(aperturePreviewError.value)
      return
    }
    aperturePreviewActive.value = true
    aperturePreviewStartedByDialog.value = true
    aperturePreviewMode.value = 'preview'
    startApertureFramePolling()
    handleApertureLedPing()
  } catch (e: any) {
    aperturePreviewError.value = e?.message || String(e)
    Message.error(`光圈预览启动失败：${aperturePreviewError.value}`)
    // 失败时 mode 保持 'preview' 但 active 仍 false，弹窗继续显示
  } finally {
    aperturePreviewLoading.value = false
  }
}

function startApertureFramePolling() {
  stopApertureFramePolling()
  // 立即刷一次
  apertureFrameTs.value = Date.now()
  apertureFrameTimer = window.setInterval(() => {
    apertureFrameTs.value = Date.now()
  }, 500)
}

function stopApertureFramePolling() {
  if (apertureFrameTimer) {
    clearInterval(apertureFrameTimer)
    apertureFrameTimer = null
  }
}

// 幂等标记：避免同一轮 close 事件中重复调 stop（@cancel / @before-close / 关闭按钮
// 都会走到 handleApertureDialogCancel 或 closeApertureDialog）。
let apertureStopping = false
async function stopAperturePreview() {
  if (apertureStopping) return
  apertureStopping = true
  try {
    // 1. 先停 frame 轮询
    stopApertureFramePolling()
    // 2. 只有本弹窗自己启动的 preview 才需要 stop preview 接口。
    //    复用现有推流（aperturePreviewStartedByDialog=false）不主动 stop，
    //    避免误停由"主摄像头输入"启动的录制。
    if (!aperturePreviewStartedByDialog.value) {
      aperturePreviewActive.value = false
      aperturePreviewMode.value = 'unknown'
      return
    }
    try {
      await http.post('/api/camera_yzt/preview/stop')
    } catch (e) {
      console.warn('[aperture] preview/stop failed:', e)
      Message.warning('摄像头预览停止失败，请稍后在系统设置中检查摄像头状态')
    } finally {
      aperturePreviewActive.value = false
      aperturePreviewStartedByDialog.value = false
      aperturePreviewMode.value = 'unknown'
    }
  } finally {
    apertureStopping = false
  }
}

// 统一的关闭弹窗入口：被"关闭"按钮 / @cancel / @before-close 调用。
// 幂等（内部 stopAperturePreview 已用 apertureStopping 防重入）。
function closeApertureDialog() {
  stopAperturePreview()
  apertureDialogVisible.value = false
}

function onApertureFrameError() {
  // 单帧 204 / 失败：不停 preview，提示"暂无预览画面"
  // 由 status / 下一轮 frame 自然恢复
  // eslint-disable-next-line no-console
  console.warn('[aperture_frame] 单帧加载失败，等待下一帧')
}

// ---- 光圈 LED 控制 ----

async function handleApertureLedPing() {
  apertureLedLoading.value = true
  try {
    const resp = await pingLed()
    apertureLedStatus.value = 'online'
    const url = resp.data?.pi_base_url || ''
    apertureLedStatusMessage.value = `树莓派 ${url} 可达`
  } catch (err: any) {
    apertureLedStatus.value = 'offline'
    apertureLedStatusMessage.value = err?.message || '树莓派光圈服务不可达'
  } finally {
    apertureLedLoading.value = false
  }
}

async function runApertureLedAction(
  name: string,
  fn: () => Promise<unknown>,
  successText: string,
) {
  apertureLedActionLoading.value = name
  try {
    await fn()
    Message.success(successText)
  } catch (err: any) {
    Message.error(err?.message || String(err))
  } finally {
    apertureLedActionLoading.value = ''
  }
}

function applyApertureColorPreset(preset: { r: number; g: number; b: number }) {
  apertureLedColor.r = preset.r
  apertureLedColor.g = preset.g
  apertureLedColor.b = preset.b
}

async function handleApertureLedStart() {
  await runApertureLedAction('start', () => startLed(), '光圈已开启')
}
async function handleApertureLedOff() {
  await runApertureLedAction('off', () => offLed(), '光圈已关闭')
}
async function handleApertureApplyColor() {
  await runApertureLedAction(
    'color',
    () => setLedColor({
      r: Number(apertureLedColor.r) || 0,
      g: Number(apertureLedColor.g) || 0,
      b: Number(apertureLedColor.b) || 0,
    }),
    '光圈颜色已更新',
  )
}
async function handleApertureApplyBrightness() {
  await runApertureLedAction(
    'brightness',
    () => setLedBrightness({ brightness: Number(apertureLedBrightness.value) || 0 }),
    '光圈亮度已更新',
  )
}

async function loadCalibrationStatus() {
  try {
    const resp = await getAlgorithmCalibrationStatus()
    calibrationStatus.value = resp.data
  } catch (e) {
    console.error('读取摄像头视角标定状态失败:', e)
  }
}

async function openCalibrationPanel() {
  calibrationVisible.value = true
  await loadCalibrationStatus()
}

/**
 * 第一阶段：标定流程由真实算法 CLI 接管。
 *
 * 流程：
 *   1. 用户点击"标定"按钮 -> 打开标定面板（摄像头捕获 + 服务器图片路径两种方式）。
 *   2. 用户从摄像头捕获一帧或填写服务器标定图片路径。
 *   3. 点击"开始标定" -> 调用 /api/algorithm/calibration。
 *   4. 成功后刷新 calibration 状态。
 */
async function runCalibrationWithImage(sourceImage: string) {
  if (!sourceImage) {
    Message.warning('请提供标定图片路径')
    return
  }
  calibrationLoading.value = true
  try {
    const resp = await runAlgorithmCalibration({
      source_image: sourceImage,
      camera_id: 'default',
      device: 'cuda',
      overwrite: true,
    })
    if (resp.data?.ok) {
      Message.success(resp.data.message || '标定完成')
    } else {
      Message.error(resp.data?.error_message || '标定失败')
    }
    await loadCalibrationStatus()
  } catch (e: any) {
    console.error('调用算法标定失败:', e)
    Message.error(e?.message || '标定失败')
  } finally {
    calibrationLoading.value = false
  }
}

// ---------- 统计图 ----------
// 1920x1080 固定缩放：浏览器 80% / 90% / 100% / 110% 缩放时内部不乱
const { fixedScreenStyle, updateFixedScreen } = useFixedScreen(1920, 1080)

const lengthChartRef = ref<HTMLElement>()
const weightChartRef = ref<HTMLElement>()
const conditionChartRef = ref<HTMLElement>()
const ratioChartRef = ref<HTMLElement>()

let charts: echarts.ECharts[] = []
let chartData = {
  length: [] as { label: string; count: number; ratio?: number }[],
  weight: [] as { label: string; count: number; ratio?: number }[],
  condition: [] as { label: string; count: number; ratio?: number }[],
  ratio: [] as { label: string; count: number; ratio?: number }[],
}

async function loadDistribution() {
  const r = computeRange()
  if (!r) return
  try {
    const resp = await http.get('/api/stats/distribution', {
      params: { start_time: r.start, end_time: r.end },
    })
    const d = resp.data
    chartData.length = d.length_distribution || []
    chartData.weight = d.weight_distribution || []
    chartData.condition = d.condition_distribution || []
    chartData.ratio = d.length_weight_ratio || []
    renderCharts()
  } catch (e) {
    console.error('加载统计分布失败:', e)
  }
}

// ---------- result mode：根据算法结果鱼列表生成分布图 ----------
function pickNumber(row: any, keys: string[]): number | null {
  if (!row) return null
  for (const k of keys) {
    const v = Number(row?.[k])
    if (Number.isFinite(v)) return v
  }
  return null
}

function buildDistributionFromValues(
  values: number[],
  bins: Array<{ label: string; min: number; max: number }>,
): Array<{ label: string; count: number }> {
  const out = bins.map((b) => ({ label: b.label, count: 0 }))
  for (const v of values) {
    for (let i = 0; i < bins.length; i++) {
      const b = bins[i]
      if (!b) continue
      if (v >= b.min && v < b.max) {
        const slot = out[i]
        if (slot) slot.count += 1
        break
      }
    }
  }
  return out
}

const LENGTH_BINS: Array<{ label: string; min: number; max: number }> = [
  { label: '<10', min: -Infinity, max: 10 },
  { label: '10-15', min: 10, max: 15 },
  { label: '15-20', min: 15, max: 20 },
  { label: '20-25', min: 20, max: 25 },
  { label: '25-30', min: 25, max: 30 },
  { label: '30-40', min: 30, max: 40 },
  { label: '>=40', min: 40, max: Infinity },
]

const WEIGHT_BINS: Array<{ label: string; min: number; max: number }> = [
  { label: '<50', min: -Infinity, max: 50 },
  { label: '50-100', min: 50, max: 100 },
  { label: '100-200', min: 100, max: 200 },
  { label: '200-400', min: 200, max: 400 },
  { label: '400-800', min: 400, max: 800 },
  { label: '>=800', min: 800, max: Infinity },
]

const CONDITION_BINS: Array<{ label: string; min: number; max: number }> = [
  { label: '<0.8', min: -Infinity, max: 0.8 },
  { label: '0.8-1.0', min: 0.8, max: 1.0 },
  { label: '1.0-1.2', min: 1.0, max: 1.2 },
  { label: '1.2-1.5', min: 1.2, max: 1.5 },
  { label: '1.5-2.0', min: 1.5, max: 2.0 },
  { label: '>=2.0', min: 2.0, max: Infinity },
]

const DAMAGE_BINS: Array<{ label: string; min: number; max: number }> = [
  { label: '0%', min: -Infinity, max: 0.0001 },
  { label: '0-5%', min: 0.0001, max: 0.05 },
  { label: '5-10%', min: 0.05, max: 0.10 },
  { label: '10-20%', min: 0.10, max: 0.20 },
  { label: '20-30%', min: 0.20, max: 0.30 },
  { label: '>=30%', min: 0.30, max: Infinity },
]

function deriveStatsFromFish(fishList: any[]) {
  const lengths: number[] = []
  const weights: number[] = []
  const conditions: number[] = []
  const damages: number[] = []
  for (const f of fishList || []) {
    const len = pickNumber(f, [
      'body_length_cm',
      'body_length',
      'length',
      'fish_length',
    ])
    if (len !== null) lengths.push(len)
    const w = pickNumber(f, ['body_weight_g', 'body_weight', 'weight'])
    if (w !== null) weights.push(w)
    const cf = pickNumber(f, ['condition_factor', 'cf'])
    if (cf !== null) conditions.push(cf)
    const dmg = pickNumber(f, [
      'surface_damage_ratio',
      'damage_ratio',
      'wound_ratio',
    ])
    if (dmg !== null) damages.push(dmg)
  }
  return {
    length: buildDistributionFromValues(lengths, LENGTH_BINS),
    weight: buildDistributionFromValues(weights, WEIGHT_BINS),
    condition: buildDistributionFromValues(conditions, CONDITION_BINS),
    ratio: buildDistributionFromValues(damages, DAMAGE_BINS),
  }
}

function applyResultModeStats(fishList: any[]) {
  try {
    if (!Array.isArray(fishList) || fishList.length === 0) {
      chartData.length = []
      chartData.weight = []
      chartData.condition = []
      chartData.ratio = []
    } else {
      const derived = deriveStatsFromFish(fishList)
      chartData.length = derived.length
      chartData.weight = derived.weight
      chartData.condition = derived.condition
      chartData.ratio = derived.ratio
    }
  } catch (err) {
    console.error('[Dashboard] applyResultModeStats failed:', err)
  }
  renderCharts()
}

function handleSelectRange(key: RangeKey) {
  selectedRangeType.value = key
  updateRangeHint()
  loadDistribution()
}

function handleCustomRangeChange() {
  updateRangeHint()
  loadDistribution()
}

function renderCharts() {
  try {
    const refs = [lengthChartRef.value, weightChartRef.value, conditionChartRef.value, ratioChartRef.value]
    const targets: [echarts.ECharts | undefined, 'length' | 'weight' | 'condition' | 'ratio', [string, string], string][] = [
      [refs[0] ? charts[0] : undefined, 'length', ['#00a8e8', '#00f5d4'], '体长区间（cm）'],
      [refs[1] ? charts[1] : undefined, 'weight', ['#00f5d4', '#00a8e8'], '体重区间（g）'],
      [refs[2] ? charts[2] : undefined, 'condition', ['#f5d567', '#f5a623'], '肥满度区间'],
      [refs[3] ? charts[3] : undefined, 'ratio', ['#f58787', '#f54646'], '体长/体重比区间'],
    ]
    for (const [chart, key, color, xAxisName] of targets) {
      if (!chart) continue
      setDistributionChartOption(chart, chartData[key], color, xAxisName)
    }
    // 关键：grid 布局下，列数变化后图表需要重新 resize
    nextTick(() => {
      charts.forEach(c => c && c.resize())
    })
  } catch (err) {
    console.error('[Dashboard] renderCharts failed:', err)
  }
}

let chartResizeObserver: ResizeObserver | null = null

function initCharts() {
  // 幂等：多次进入 result 模式 / 反复跳路由都不会重复初始化 ECharts。
  if (charts.length > 0) return
  try {
    const refs = [lengthChartRef.value, weightChartRef.value, conditionChartRef.value, ratioChartRef.value]
    charts = refs.map(r => r ? echarts.init(r) : null).filter(Boolean) as echarts.ECharts[]

    // 用 ResizeObserver 监听容器尺寸变化（窗口缩放、grid 换行、左侧栏 reflow）
    if (typeof ResizeObserver !== 'undefined') {
      try {
        chartResizeObserver = new ResizeObserver(() => {
          charts.forEach(c => c && c.resize())
        })
        refs.forEach(r => r && chartResizeObserver && chartResizeObserver.observe(r))
      } catch (e) {
        console.warn('ResizeObserver init failed:', e)
      }
    }
  } catch (err) {
    // 任何 ECharts 初始化异常都不能让整个 Dashboard 整页黑屏。
    console.error('[Dashboard] initCharts failed:', err)
    charts = []
  }
}

// ---------- result mode watchers ----------
// 重要：这些 watch 会访问 chartData / charts / initCharts / renderCharts / applyResultModeStats，
// 必须放在这些变量和函数声明之后，避免 immediate watch 在 setup 阶段提前执行导致 TDZ。
// （原位置在 isResultMode computed 附近，会触发
//  ReferenceError: can't access lexical declaration 'chartData' before initialization）

// 切到新 video_id 时清空选中 / 输入。
watch(
  () => [route.query.mode, route.query.video_id],
  () => {
    // 防御：如果摄像头实时输入正在运行，本 watch 不应重置 result 模式展示状态
    // 也不应清空左侧统计图（实时大屏模式下统计图由 loadDistribution 维护）。
    // 这是 switchToCameraLiveMode 之后 router.replace 清空 query 时的关键守卫。
    if (
      cameraStreamActive.value ||
      cameraState.value === 'starting' ||
      cameraState.value === 'running'
    ) {
      return
    }
    resultTrackIdInput.value = ''
    resultSelectedTrackId.value = ''
    if (isResultMode.value) {
      // result 模式下重置左侧统计图，避免上个 video 的图表残留
      chartData.length = []
      chartData.weight = []
      chartData.condition = []
      chartData.ratio = []

      // ECharts 实例此时可能还没初始化（mount 后 watch 立即触发），
      // nextTick 后再尝试渲染。
      nextTick(() => {
        try {
          if (charts.length === 0) {
            initCharts()
          }
        } catch (e) {
          console.warn('[Dashboard route watch initCharts in result mode]', e)
        }

        try {
          renderCharts()
        } catch (e) {
          console.warn('[Dashboard route watch renderCharts]', e)
        }
      })
    }
    try {
      loadResultMode()
    } catch (e) {
      console.warn('[Dashboard route watch loadResultMode]', e)
    }
  },
  { immediate: true }
)

// result 模式：根据 resultFishList 派生左侧 4 个分布图。
// 普通大屏模式下 resultFishList 为空，watch 不会做任何有效覆盖。
watch(
  resultFishList,
  (list) => {
    if (!isResultMode.value) return
    nextTick(() => {
      try {
        if (charts.length === 0) {
          initCharts()
        }
      } catch (e) {
        console.warn('[Dashboard fishList watch initCharts]', e)
      }
      try {
        applyResultModeStats(list)
      } catch (e) {
        console.warn('[Dashboard fishList watch applyResultModeStats]', e)
      }
    })
  },
  { immediate: true }
)

// 启动时如果 localStorage 有当前任务，自动恢复（**包括 done 的最终结果**）
async function restoreFromLocalStorage() {
  const saved = readCurrentTaskFromStorage()
  if (!saved) return
  currentVideoId.value = saved.videoId
  currentSourceType.value = saved.sourceType
  try {
    const resp = await http.get(`/api/videos/${saved.videoId}`)
    applyVideoTaskToUi(resp.data)
    if (resp.data.status === 'done' || resp.data.status === 'no_fish_detected' || resp.data.status === 'failed' || resp.data.status === 'stopped') {
      // 终止态：保留最终结果（不再额外拉鱼列表，鱼信息通过点击鱼框触发）
    } else {
      // 处理中：继续轮询
      pollVideoTask(saved.videoId)
    }
  } catch (e) {
    // 任务已被删除等情况，清除 localStorage
    clearCurrentTaskFromStorage()
    currentVideoId.value = ''
  }
}

async function restoreCameraState() {
  try {
    const resp = await http.get('/api/camera_yzt/status')
    const data = resp.data
    const state = data.state || 'idle'
    const streamReady = data.stream_ready === true || (data.frames_captured || 0) > 0
    const isLive =
      data.is_active ||
      state === 'starting' ||
      state === 'running'

    // 同步定时录制字段；恢复倒计时卡片 / 停止原因卡片都要用到
    applyCameraTimingFromStatus(data)

    if (isLive && data.video_id) {
      cameraStreamActive.value = true
      cameraConnected.value = state === 'running' && streamReady
      cameraState.value = state
      currentVideoId.value = data.video_id
      currentSourceType.value = 'rtsp'
      saveCurrentTaskToStorage(data.video_id, 'rtsp')
      if (streamReady) {
        startCameraFramePolling()
      }
      startedAtMs = data.started_at ? new Date(data.started_at).getTime() : Date.now()
      finishedAtMs = null
      startTime.value = data.started_at ? formatChinaDateTime(data.started_at) : formatChinaDateTime(Date.now())
      endTime.value = null
      duration.value = '00:00:00'
      startDurationCounter()
      // 恢复倒计时本地 1 秒递减 timer（后端 /status 仍是权威值）
      if (timedRecording.value) {
        startRemainingTick()
      } else {
        stopRemainingTick()
      }
      pollCameraStatus()
      return
    }

    // 后端处于 error，但存在 video_id：保留 video_id 让用户能看到失败信息
    if (state === 'error' && data.video_id) {
      cameraStreamActive.value = false
      cameraConnected.value = false
      cameraState.value = 'error'
      currentVideoId.value = data.video_id
      currentSourceType.value = 'rtsp'
      saveCurrentTaskToStorage(data.video_id, 'rtsp')
      Message.error(
        data.startup_error ||
          data.last_error ||
          '摄像头启动失败，请检查树莓派推流与后端读流地址',
      )
      return
    }

    // 后端 idle：保持 stopReason / stopSource 用于展示已停止原因
    stopRemainingTick()
  } catch (e) {
    console.error('恢复摄像头状态失败:', e)
  }
}

/**
 * 从 store/video 恢复历史跳转的视频状态。
 *
 * 优先级：
 *   1) restoreFromLocalStorage()（用户手动上传 / 摄像头）
 *   2) restoreFromVideoStore()（从 /admin/video-history "跳转" 进入）
 *
 * 注意：不清空 store/video；如果 store 里没有 videoSrc 也不做任何事。
 */
async function restoreFromVideoStore() {
  // 如果摄像头在跑，不接管
  if (cameraStreamActive.value) return

  const v = getVideoState()
  if (!v.videoSrc || !v.videoId) return

  // 不覆盖 currentVideoId（如果用户已经在跑摄像头）
  if (currentVideoId.value && currentVideoId.value !== v.videoId) {
    return
  }

  currentVideoId.value = v.videoId
  currentSourceType.value = v.source === 'history' ? 'history' : (v.source || 'history')
  saveCurrentTaskToStorage(v.videoId, currentSourceType.value)

  // 拉任务元数据（status / fish_count / started_at 等）
  try {
    const resp = await http.get(`/api/videos/${v.videoId}`)
    applyVideoTaskToUi(resp.data)
  } catch (e) {
    console.warn('restoreFromVideoStore: fetch video task failed', e)
  }

  // 同步到右侧"鱼 ID 检索"面板
  try {
    const vid = String(v.videoId || '')
    if (vid) {
      fishQueryVideoId.value = vid
      // 先把视频列表加载好（避免下拉为空），再加载鱼
      await loadDashboardFishQueryVideos()
      await loadDashboardFishQueryFishes(vid)
    }
  } catch (e) {
    console.warn('restoreFromVideoStore: sync fish query failed', e)
  }
}

watch(
  () => route.query.video_id,
  async (vid) => {
    const v = String(vid || '').trim()
    if (!v) return
    // 防御：摄像头实时输入运行中，不应被旧 result 路由 query 触发重新加载鱼列表
    if (
      cameraStreamActive.value ||
      cameraState.value === 'starting' ||
      cameraState.value === 'running'
    ) {
      return
    }
    if (fishQueryVideoId.value === v) return
    fishQueryVideoId.value = v
    try {
      await loadDashboardFishQueryVideos()
      await loadDashboardFishQueryFishes(v)
    } catch (e) {
      console.warn('[Dashboard watch video_id] sync fish query failed:', e)
    }
  },
)

// 兜底：任何子组件渲染错误都不让整个 Dashboard 整页黑屏。
onErrorCaptured((err: unknown, instance, info) => {
  console.error('[Dashboard onErrorCaptured]', info, err)
  // 返回 false 让错误继续向上冒泡，避免静默吞掉真正的代码缺陷
  // 但 at least 我们已经记录，问题排查有依据
  return false
})

onMounted(async () => {
  // result 模式（?mode=result&video_id=...）只展示算法结果，
  // 不做实时摄像头恢复 / 启动 / 拉流。
  if (isResultMode.value) {
    try {
      // 初始化左侧 4 个 ECharts 容器
      // （result 模式以前不调用 initCharts，导致左侧统计图空白）
      try {
        initCharts()
      } catch (e) {
        console.error('[Dashboard onMounted init charts in result mode]', e)
      }

      // 加载当前视频的算法结果
      loadResultMode()
      // 仍然加载一次 chart 数据，但避免占用摄像头资源
      selectedRangeType.value = 'today'
      updateRangeHint()
      // 注意：result 模式下不去请求聚合 /api/stats/distribution，
      // 改用 resultFishList 实时派生统计图（见 watch(resultFishList)）。
      loadCalibrationStatus()
      // 右侧鱼 ID 检索面板：自动带入 resultVideoId
      try {
        const vid = String(route.query.video_id || '')
        if (vid) {
          fishQueryVideoId.value = vid
        }
        // 先把视频列表（含当前 video_id）加载好，再去拉鱼
        await loadDashboardFishQueryVideos()
        if (vid) await loadDashboardFishQueryFishes(vid)
      } catch (e) {
        console.warn('[Dashboard onMounted result fish query]', e)
      }
      window.addEventListener('resize', () => {
        charts.forEach(chart => chart.resize())
      })
    } catch (e) {
      // 防御：任何一个步骤出错都不要让页面 setup 崩溃。
      console.error('[Dashboard onMounted result mode]', e)
    }
    return
  }
  try {
    initCharts()
    selectedRangeType.value = 'today'
    updateRangeHint()
    loadDistribution()
  } catch (e) {
    console.error('[Dashboard onMounted init charts]', e)
  }
  try { restoreFromLocalStorage() } catch (e) { console.error('[Dashboard onMounted restore local]', e) }
  try { restoreCameraState() } catch (e) { console.error('[Dashboard onMounted restore camera]', e) }
  // 摄像头未启动时，从 store/video 接管（历史跳转等）
  if (!cameraStreamActive.value) {
    try { restoreFromVideoStore() } catch (e) { console.error('[Dashboard onMounted restore video store]', e) }
  }
  try { loadCalibrationStatus() } catch (e) { console.error('[Dashboard onMounted load calib]', e) }
  window.addEventListener('resize', () => {
    charts.forEach(chart => chart.resize())
  })
})

onUnmounted(() => {
  pollAbort = true
  clearAllTimers()
  stopCameraFramePolling()
  // 光圈预览：只有本弹窗自己启动的 preview 才 stop preview 接口，
  // 避免误停由"主摄像头输入"启动的录制。
  // 异步调用 fire-and-forget；onUnmounted 不 await。
  if (aperturePreviewStartedByDialog.value) {
    http
      .post('/api/camera_yzt/preview/stop')
      .catch((e) => console.warn('[aperture] preview/stop on unmount failed:', e))
    aperturePreviewActive.value = false
    aperturePreviewStartedByDialog.value = false
  }
  if (chartResizeObserver) {
    chartResizeObserver.disconnect()
    chartResizeObserver = null
  }
  charts.forEach(chart => chart.dispose())
})
</script>

<style lang="scss" scoped>
/* 1920x1080 固定比例缩放的外层 viewport：撑满浏览器窗口，不滚动 */
.dashboard-viewport {
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  position: relative;
  background: #000;
}

/* 内层 fixed 容器：固定 1920x1080 设计尺寸，通过 transform: scale 等比缩放 */
.dashboard-fixed {
  position: absolute;
  width: 1920px;
  height: 1080px;
  overflow: hidden;
  left: 0;
  top: 0;
  transform-origin: top left;
}
.dashboard-background {
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: url('@/assets/images/page_bg.png') no-repeat center center;
  background-size: cover;
  position: relative;
}
.dashboard-background::before {
  content: '';
  position: absolute;
  inset: 0;
  background: rgb(0 0 0 / 40%);
  z-index: 0;
}
.dashboard-background > * {
  position: relative;
  z-index: 1;
}
.left-panel-wrapper {
  height: 100%;
  :deep(.panel-body) {
    height: calc(100% - 65px);
    padding: 8px;
  }
}
.center-panel-wrapper {
  height: 100%;
  :deep(.panel-body) {
    height: calc(100% - 65px);
    padding: 8px;
  }
}
.right-panel-wrapper {
  height: 100%;
  min-width: 0;
  max-width: 100%;
  box-sizing: border-box;
  overflow: hidden;
  :deep(.panel-body) {
    height: calc(100% - 65px);
    min-width: 0;
    min-height: 0;
    padding: 8px;
    overflow: hidden;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
  }
}
.center-panel-wrapper {
  height: 100%;
  :deep(.panel-body) {
    height: calc(100% - 65px);
    padding: 8px;
  }
}
.left-panel {
  height: 100%;
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 4px;
  overflow-y: auto;
  .time-range-bar {
    flex: 0 0 auto;
    padding: 8px 10px;
    background: rgb(26, 26, 46, 70%);
    border: 1px solid rgb(0, 168, 232, 30%);
    border-radius: 12px;
    .range-buttons {
      display: grid;
      grid-template-columns: repeat(5, 1fr);
      gap: 6px;
    }
    .range-btn {
      padding: 6px 4px;
      font-size: 12px;
      border: 1px solid rgb(255, 255, 255, 12%);
      border-radius: 6px;
      background: rgb(255, 255, 255, 6%);
      color: rgb(255, 255, 255, 75%);
      cursor: pointer;
      transition: all 0.2s;
      white-space: nowrap;
      &:hover {
        background: rgb(0, 168, 232, 18%);
        color: #fff;
      }
      &.active {
        // 深蓝色背景 + 白色文字
        background: linear-gradient(135deg, #165dff, #0e3fa0);
        border-color: #165dff;
        color: #fff;
        font-weight: 600;
      }
    }
    .range-hint {
      display: block;
      margin-top: 8px;
      color: rgb(255, 255, 255, 60%);
      font-size: 11px;
      font-family: SarasaMonoSC, monospace;
    }
  }
  .charts-grid {
    flex: 1 1 auto;
    min-height: 0;
    display: grid;
    grid-template-columns: 1fr;
    grid-template-rows: repeat(4, minmax(0, 1fr));
    gap: 10px;
    height: 100%;
    overflow: hidden;
  }
  .result-stats-empty {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    pointer-events: none;
    color: rgb(255, 255, 255, 70%);
    font-size: 12px;
  }
  .chart-card {
    background: rgb(26, 26, 46, 70%);
    border: 1px solid rgb(0, 168, 232, 30%);
    border-radius: 16px;
    padding: 10px 12px;
    display: flex;
    flex-direction: column;
    min-height: 0;
    overflow: hidden;
    .card-title {
      color: rgb(255, 255, 255, 90%);
      font-size: 14px;
      font-weight: 500;
      margin-bottom: 8px;
      padding-left: 10px;
      border-left: 3px solid #00a8e8;
      flex: 0 0 auto;
    }
    .chart-container {
      flex: 1 1 auto;
      min-height: 0;
      width: 100%;
    }
  }
}
.center-panel {
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 12px;
  .top-info-bar {
    display: flex;
    justify-content: space-between;
    padding: 10px 16px;
    background: rgb(26, 26, 46, 70%);
    border: 1px solid rgb(0, 168, 232, 30%);
    border-radius: 16px;
    .info-left, .info-right {
      display: flex;
      gap: 24px;
    }
    .info-item {
      .label {
        color: rgb(255, 255, 255, 60%);
        font-size: 16px;
      }
      .value {
        color: #00a8e8;
        font-size: 13px;
        font-family: SarasaMonoSC, monospace;
      }
    }
  }
  .status-cards {
    display: flex;
    gap: 16px;
    .status-card {
      flex: 1;
      display: flex;
      align-items: center;
      gap: 16px;
      padding: 16px 20px;
      background: rgb(26, 26, 46, 70%);
      border: 1px solid rgb(0, 168, 232, 30%);
      border-radius: 16px;
      .status-icon {
        width: 48px;
        height: 48px;
        display: flex;
        align-items: center;
        justify-content: center;
        background: linear-gradient(135deg, rgb(0, 168, 232, 30%), rgb(0, 245, 212, 20%));
        border-radius: 12px;
        font-size: 20px;
        color: #00a8e8;
      }
      .status-content {
        flex: 1;
        min-width: 0;
        .status-label {
          color: rgb(255, 255, 255, 60%);
          font-size: 12px;
          margin-bottom: 4px;
        }
        .status-value {
          color: #fff;
          font-size: 22px;
          font-weight: bold;
          font-family: SarasaMonoSC, monospace;
          small {
            font-size: 12px;
            color: rgb(255, 255, 255, 50%);
            margin-left: 4px;
          }
          &.camera-status {
            &.connected { color: #00f5d4; }
            &.waiting { color: #f5a623; }
          }
        }
        .timed-row {
          display: grid;
          grid-template-columns: 60px minmax(0, 1fr);
          gap: 8px;
          align-items: center;
          font-size: 12px;
          font-weight: 400;
          font-family: SarasaMonoSC, monospace;
          line-height: 1.4;
          .k {
            color: rgb(255 255 255 / 55%);
            min-width: 0;
          }
          .v {
            color: rgb(255 255 255 / 82%);
            font-weight: 500;
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
        }
        .timed-running {
          display: flex;
          flex-direction: column;
          gap: 5px;
          .timed-remaining {
            display: flex;
            align-items: baseline;
            gap: 8px;
            margin: 0;
            .remaining-label {
              color: rgb(255 255 255 / 58%);
              font-size: 12px;
              font-weight: 400;
              line-height: 1.35;
              letter-spacing: 0.5px;
            }
            .remaining-value {
              color: rgb(0 245 212 / 88%);
              font-size: 18px;
              font-weight: 600;
              line-height: 1.15;
              font-family: SarasaMonoSC, monospace;
              text-shadow: none;
            }
          }
          .timed-progress {
            margin: 2px 0 4px;
            .timed-progress-bar {
              width: 100%;
              height: 4px;
              background: rgb(255 255 255 / 9%);
              border-radius: 4px;
              overflow: hidden;
            }
            .timed-progress-fill {
              height: 100%;
              background: linear-gradient(90deg, rgb(0 168 232 / 85%), rgb(0 245 212 / 85%));
              border-radius: 4px;
              transition: width 0.6s ease;
            }
            .timed-progress-text {
              display: flex;
              justify-content: space-between;
              margin-top: 3px;
              font-size: 11px;
              font-weight: 400;
              color: rgb(255 255 255 / 52%);
              line-height: 1.35;
              font-family: SarasaMonoSC, monospace;
            }
          }
        }
        .timed-pending {
          display: flex;
          flex-direction: column;
          gap: 5px;
          .timed-pending-desc {
            font-size: 12px;
            line-height: 1.5;
            color: rgb(255 255 255 / 72%);
          }
        }
        .timed-stopped {
          font-size: 12px;
          font-weight: 400;
          color: rgb(255 255 255 / 78%);
          .timed-stopped-desc {
            font-size: 12px;
            line-height: 1.45;
            color: rgb(255 255 255 / 66%);
            margin-bottom: 5px;
          }
        }
      }
      &.timed-recording-card {
        border-color: rgb(0 168 232 / 24%);
        &.is-pending .status-icon {
          background: rgb(245 166 35 / 16%);
          color: #f5a623;
        }
        &.is-running .status-icon {
          background: rgb(245 166 35 / 16%);
          color: #f5a623;
        }
        &.is-auto-stopped .status-icon {
          background: rgb(0 168 232 / 16%);
          color: #00f5d4;
        }
        &.is-manual-stopped .status-icon {
          background: rgb(156 163 175 / 16%);
          color: #9ca3af;
        }
      }

      // 统一覆盖：定时录制卡片视觉跟随 center-panel 其它状态卡，
      // 不再做大号仪表盘效果。remaining-value 不超过 20px，青色只用于剩余时间。
      &.timed-recording-card {
        min-height: 0;
        align-items: flex-start;
        gap: 10px;
        padding: 12px 14px;
        border-color: rgb(0 168 232 / 24%);
        background: rgb(26 26 46 / 70%);
      }
      &.timed-recording-card .status-icon {
        width: 38px;
        height: 38px;
        font-size: 16px;
        flex: 0 0 38px;
      }
      &.timed-recording-card .status-content {
        min-width: 0;
        width: 100%;
      }
      &.timed-recording-card .status-label {
        color: rgb(255 255 255 / 72%);
        font-size: 13px;
        font-weight: 500;
        line-height: 1.35;
        margin-bottom: 4px;
      }
      &.timed-recording-card .status-value {
        color: rgb(255 255 255 / 82%);
        font-size: 13px;
        font-weight: 400;
        line-height: 1.45;
      }
    }
  }
  .timed-dialog {
    // 颜色由底部 <style lang="scss"> 非 scoped 块统一控制（限定在 .timed-recording-modal 内），
    // 这里只保留布局相关样式，避免双声明。
    .timed-dialog-desc {
      font-size: 13px;
      line-height: 1.6;
      padding: 10px 12px;
      border-radius: 8px;
      margin-bottom: 10px;
    }
    .timed-dialog-tip {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      margin-bottom: 14px;
      border-radius: 8px;
      font-size: 12px;
      line-height: 1.5;
      i {
        font-size: 13px;
        flex: 0 0 auto;
      }
    }
    .timed-dialog-group {
      margin-bottom: 10px;
      .group-label {
        font-size: 12px;
        margin-bottom: 6px;
        letter-spacing: 0.5px;
      }
      .group-buttons {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
      }
    }
    .timed-dialog-custom {
      .label {
        font-size: 13px;
        margin-bottom: 8px;
      }
      .custom-row {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
        .sep {
          font-size: 13px;
        }
      }
      .custom-summary {
        margin-top: 10px;
        font-size: 13px;
        .summary-value {
          font-weight: bold;
          font-family: SarasaMonoSC, monospace;
        }
      }
      .timed-dialog-error {
        display: flex;
        align-items: center;
        gap: 6px;
        margin-top: 10px;
        padding: 8px 12px;
        border-radius: 6px;
        font-size: 13px;
      }
    }
  }
  .video-area {
    flex: 1;
    min-height: 0;
    background: rgb(26, 26, 46, 70%);
    border: 1px solid rgb(0, 168, 232, 30%);
    border-radius: 16px;
    padding: 8px;
    .video-wrapper {
      width: 100%;
      height: 100%;
      min-height: 0;
      border-radius: 12px;
      overflow: hidden;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .camera-stream-container {
      width: 100%;
      height: 100%;
      border-radius: 12px;
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }
    .camera-stream-img {
      flex: 1;
      width: 100%;
      object-fit: contain;
      cursor: crosshair;
    }
    .camera-statusbar {
      display: flex;
      flex-direction: column;
      gap: 6px;
      font-size: 12px;
      padding: 8px;
      background: rgb(26, 26, 46, 70%);
      border-top: 1px solid rgb(255, 255, 255, 10%);
      .status {
        display: flex;
        gap: 6px;
        .k {
          opacity: 0.7;
          flex: 0 0 auto;
        }
        .v, code {
          opacity: 0.95;
          flex: 1;
          min-width: 0;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        code {
          color: #f5a623;
          font-family: monospace;
          white-space: pre-wrap;
          word-break: break-all;
          font-size: 10px;
        }
      }
    }
  }
  .control-buttons {
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 16px;
    padding: 12px;
    flex-wrap: wrap;
    .camera-group {
      display: flex;
      align-items: center;
      gap: 0;
      .ctrl-btn {
        border-radius: 12px 0 0 12px;
        &.settings-btn {
          padding: 12px 14px;
          border-radius: 0 12px 12px 0;
          border-left: none;
          margin-left: 1px;
        }
      }
    }
    .ctrl-btn {
      padding: 12px 24px;
      border-radius: 12px;
      border: 1px solid rgb(0, 168, 232, 50%);
      background: linear-gradient(135deg, rgb(0, 168, 232, 20%), rgb(0, 245, 212, 10%));
      color: #fff;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 14px;
      transition: all 0.3s ease;
      &:hover {
        background: linear-gradient(135deg, rgb(0, 168, 232, 40%), rgb(0, 245, 212, 20%));
        transform: translateY(-2px);
      }
      &:disabled {
        opacity: 0.4;
        cursor: not-allowed;
      }
      &.upload-btn {
        background: linear-gradient(135deg, #00a8e8, #00f5d4);
        border: none;
      }
      &.camera-btn {
        background: linear-gradient(135deg, #8b5cf6, #a78bfa);
        border: none;
      }
      &.detect-btn {
        background: linear-gradient(135deg, #f5a623, #f5d567);
        border: none;
      }
      &.clear-btn {
        background: linear-gradient(135deg, #6b7280, #9ca3af);
        border: none;
      }
      &.calibration-btn {
        background: linear-gradient(135deg, #0ea5e9, #22d3ee);
        border: none;
      }
      .calibration-panel {
        display: flex;
        flex-direction: column;
        gap: 14px;
        color: #1d2129;
        .calibration-status-row {
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .label {
          font-size: 13px;
          color: #4e5969;
          flex: 0 0 auto;
        }
        .calibration-desc {
          padding: 12px;
          border-radius: 8px;
          background: #f7f8fa;
          color: #4e5969;
          font-size: 13px;
          line-height: 1.7;
        }
        .calibration-path {
          display: flex;
          gap: 8px;
          align-items: flex-start;
          font-size: 12px;
          code {
            flex: 1;
            word-break: break-all;
            color: #1677ff;
            background: #f2f3f5;
            padding: 4px 6px;
            border-radius: 4px;
          }
        }
        .calibration-actions {
          display: flex;
          gap: 10px;
          justify-content: flex-end;
          margin-top: 4px;
        }
        .calibration-preview {
          margin-top: 8px;
          display: flex;
          justify-content: center;
          background: #000;
          border-radius: 6px;
          padding: 6px;
          max-height: 320px;
          img {
            max-width: 100%;
            max-height: 320px;
            object-fit: contain;
          }
        }
        .calibration-preview-grid {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 10px;
          margin-top: 6px;
          .calibration-preview-cell {
            background: #f7f8fa;
            border-radius: 6px;
            padding: 6px;
            .cell-title {
              font-size: 12px;
              font-weight: 500;
              color: #4e5969;
              margin-bottom: 4px;
              text-align: center;
            }
            img {
              width: 100%;
              max-height: 240px;
              object-fit: contain;
              border-radius: 4px;
              background: #000;
            }
            .calibration-hint {
              text-align: center;
              color: #86909c;
              font-size: 12px;
              padding: 30px 0;
            }
          }
        }
        .calibration-warn {
          margin-top: 6px;
          font-size: 12px;
          color: #ff7d00;
          background: #fff7e6;
          padding: 6px 8px;
          border-radius: 4px;
        }
      }
    }
  }
}
.right-panel {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  .right-panel-content {
    flex: 1 1 auto;
    min-height: 0;
    overflow: hidden auto;
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 4px;
    padding-right: 6px;
  }
}
.right-fish-search-panel {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  box-sizing: border-box;
  height: 100%;
  min-height: 0;
  display: grid;
  grid-template-rows: auto minmax(180px, 32%) minmax(0, 1fr);
  gap: 10px;
  overflow: hidden;
  padding: 4px;
}
.fish-query-card,
.fish-image-card,
.fish-metrics-card {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  border-radius: 12px;
  background: rgb(6 18 38 / 72%);
  border: 1px solid rgb(47 123 255 / 28%);
  box-shadow: 0 8px 20px rgb(32 91 179 / 8%);
  padding: 12px;
  box-sizing: border-box;
  overflow: hidden;
}
.fish-query-card .card-title,
.fish-image-card .card-title,
.fish-metrics-card .card-title {
  color: rgb(255 255 255 / 90%);
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 8px;
  padding-left: 8px;
  border-left: 3px solid #00d4e6;
}
.fish-query-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
.query-row {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  box-sizing: border-box;
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: 8px;
  align-items: center;
}
.query-row label,
.query-row .query-label {
  min-width: 0;
  white-space: nowrap;
  color: rgb(255 255 255 / 70%);
  font-size: 13px;
  text-align: right;
}
.query-row :deep(.arco-picker),
.query-row :deep(.arco-select),
.query-row :deep(.arco-select-view),
.query-row :deep(.arco-input-wrapper) {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  box-sizing: border-box;
}
.fish-image-card {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}
.dashboard-fish-image-box {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  flex: 1;
  min-height: 0;
  border-radius: 8px;
  background: #061226;
  border: 1px solid rgb(47 123 255 / 35%);
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}
.dashboard-fish-image-box img {
  display: block;
  max-width: 92%;
  max-height: 92%;
  object-fit: contain;
}
.fish-image-empty {
  color: rgb(255 255 255 / 55%);
  font-size: 13px;
  text-align: center;
  padding: 24px 12px;
}
.fish-metrics-card {
  min-width: 0;
  min-height: 0;
  overflow: auto;
}
.fish-metrics-camera-notice {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px;
  border-radius: 8px;
  background: rgb(0 168 232 / 10%);
  border: 1px solid rgb(0 168 232 / 25%);
  color: rgb(255 255 255 / 85%);
  i {
    flex: 0 0 auto;
    color: #00a8e8;
    font-size: 18px;
    line-height: 1.4;
  }
  .notice-text {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .notice-title {
    font-size: 14px;
    font-weight: 600;
    color: #00f5d4;
  }
  .notice-desc {
    font-size: 12px;
    line-height: 1.6;
    color: rgb(255 255 255 / 70%);
  }
}
.fish-metrics-grid {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 8px;
}
.metric-row {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  box-sizing: border-box;
  display: grid;
  grid-template-columns: minmax(80px, auto) minmax(0, 1fr);
  gap: 8px;
  align-items: center;
  padding: 8px 10px;
  border-radius: 8px;
  background: rgb(255 255 255 / 6%);
  border: 1px solid rgb(255 255 255 / 8%);
}
.metric-row .value,
.metric-row .metric-value {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: right;
  color: rgb(0 245 212);
  font-family: SarasaMonoSC, monospace;
  font-size: 13px;
}
.metric-row .metric-value small {
  color: rgb(255 255 255 / 50%);
  font-size: 11px;
}
.metric-row .metric-value.damage {
  color: #ff8a4c;
}
.metric-label {
  color: rgb(255 255 255 / 70%);
  font-size: 13px;
  min-width: 0;
}
.video-status-text {
  color: rgb(255, 255, 255, 80%);
  font-size: 14px;
  text-align: center;
  padding: 40px 0;
  &.error {
    color: rgb(255, 132, 132);
  }
}
.result-video-wrap {
  position: relative;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border-radius: 12px;
  background: #000;
  display: flex;
  align-items: center;
  justify-content: center;
}
.grounded-sam-video {
  width: 100%;
  height: 100%;
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  display: block;
  background: #000;
}
.result-video-meta {
  position: absolute;
  left: 12px;
  right: 12px;
  bottom: 10px;
  z-index: 3;
  display: flex;
  gap: 6px;
  padding: 6px 10px;
  border-radius: 8px;
  background: rgb(0 0 0 / 45%);
  font-size: 12px;
  color: rgb(255 255 255 / 85%);
  pointer-events: none;
}
.result-video-meta .meta-label {
  opacity: 0.7;
  flex: 0 0 auto;
}
.result-video-meta .meta-value {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: SarasaMonoSC, monospace;
  color: rgb(0, 200, 230);
}

/* ---- 光圈设置弹窗 ---- */
.aperture-dialog {
  display: grid;
  grid-template-columns: 1.2fr 1fr;
  gap: 18px;
  min-height: 380px;
}
.aperture-preview {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: #000;
  border-radius: 8px;
  overflow: hidden;
  min-height: 360px;
}
.aperture-frame-img {
  width: 100%;
  height: 360px;
  object-fit: contain;
  background: #000;
}
.aperture-preview-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: rgb(255, 255, 255, 70%);
  font-size: 13px;
  min-height: 360px;
  i {
    font-size: 24px;
  }
  &.error {
    color: #f53f3f;
    background: rgb(245 63 63 / 8%);
  }
}
.aperture-preview-tip {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  padding: 6px 10px;
  background: rgb(0 0 0 / 45%);
  color: rgb(255 255 255 / 80%);
  font-size: 11px;
  text-align: center;
  pointer-events: none;
}
.aperture-controls {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 460px;
  overflow-y: auto;
  padding-right: 4px;
}
.aperture-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.aperture-section-title {
  font-size: 13px;
  font-weight: 600;
  color: #1d2129;
  display: flex;
  align-items: center;
  gap: 8px;
}
.aperture-section-desc {
  font-size: 12px;
  color: #86909c;
  line-height: 1.5;
}
.aperture-color-presets {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.aperture-color-preset {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  font-size: 12px;
  border: 1px solid #e5e6eb;
  border-radius: 6px;
  background: #fff;
  color: #4e5969;
  cursor: pointer;
  transition: all 0.15s ease;
  font-family: inherit;
}
.aperture-color-preset:hover {
  border-color: #165dff;
  color: #165dff;
}
.aperture-color-dot {
  display: inline-block;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 1px solid rgb(0 0 0 / 12%);
}
.aperture-rgb-row {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}
.aperture-color-preview {
  width: 100%;
  height: 24px;
  border-radius: 6px;
  border: 1px solid #e5e6eb;
  transition: background-color 0.15s ease;
}
.aperture-mode-tip {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 8px 10px;
  font-size: 12px;
  line-height: 1.5;
  border-radius: 6px;
  color: #1d2129;
  background: rgb(245 245 245 / 80%);
  border: 1px solid #e5e6eb;
  i {
    flex: 0 0 auto;
    margin-top: 1px;
  }
  &.aperture-mode-preview {
    color: #4e5969;
    background: rgb(245 245 245 / 80%);
    border-color: #e5e6eb;
  }
  &.aperture-mode-recording-reuse {
    color: #165dff;
    background: rgb(22 93 255 / 8%);
    border-color: rgb(22 93 255 / 20%);
  }
  &.aperture-mode-unknown {
    color: #86909c;
    background: rgb(245 245 245 / 80%);
    border-color: #e5e6eb;
  }
}
.aperture-dialog-footer {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 8px;
  padding-top: 14px;
  margin-top: 4px;
  border-top: 1px solid #e5e6eb;
}

/* 小屏：单列 */
@media (width <= 768px) {
  .aperture-dialog {
    grid-template-columns: 1fr;
  }
}
</style>

<!--
  定时录制弹窗：复用 Arco 默认主题，与 CameraSettings 弹窗视觉一致。
  Arco Modal 默认 #fff 背景 + #1d2129 标题 + #165dff 主按钮。
  这里只补：弹窗内自有组件（preset-btn / desc / tip / error 等），
  不再覆盖 Arco Modal 外壳 / 主按钮，避免与 CameraSettings 弹窗风格冲突。
  所有规则以 .timed-recording-modal 限定作用域，不污染其它弹窗。
-->
<style lang="scss">
/* ---------- 弹窗内部结构：颜色与 CameraSettings 弹窗保持一致 ---------- */
.timed-recording-modal .timed-dialog {
  color: #1d2129;
}
.timed-recording-modal .timed-dialog-desc {
  color: #4e5969;
  background: rgb(245 245 245 / 80%);
  border: 1px solid #e5e6eb;
}
.timed-recording-modal .timed-dialog-tip {
  color: #4e5969;
  background: rgb(22 93 255 / 6%);
  border: 1px solid rgb(22 93 255 / 20%);
  i {
    color: #165dff;
  }
}
.timed-recording-modal .timed-dialog-group .group-label {
  color: #86909c;
}
.timed-recording-modal .timed-dialog-custom .label {
  color: #1d2129;
}
.timed-recording-modal .timed-dialog-custom .custom-row .sep {
  color: #4e5969;
}
.timed-recording-modal .timed-dialog-custom .custom-summary {
  color: #4e5969;
  .summary-value {
    color: #165dff;
  }
}
.timed-recording-modal .timed-dialog-error {
  color: #f53f3d;
  background: rgb(245 63 63 / 6%);
  border: 1px solid rgb(245 63 63 / 20%);
  i {
    color: #f53f3d;
  }
}

/* ---------- preset-btn：与 Arco 主色 #165dff 保持一致 ---------- */
.timed-recording-modal .preset-btn {
  position: relative;
  padding: 6px 14px;
  min-width: 86px;
  font-size: 13px;
  color: #1d2129;
  background: #fff;
  border: 1px solid #e5e6eb;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s ease;
  font-family: inherit;
}
.timed-recording-modal .preset-btn:hover {
  color: #165dff;
  border-color: #165dff;
}
.timed-recording-modal .preset-btn.active,
.timed-recording-modal .preset-btn.is-active {
  color: #fff;
  background: #165dff;
  border-color: #165dff;
  font-weight: bold;
}
.timed-recording-modal .preset-btn .cap-tag {
  margin-left: 6px;
  padding: 1px 6px;
  font-size: 10px;
  color: #86909c;
  background: rgb(245 245 245 / 80%);
  border: 1px solid #e5e6eb;
  border-radius: 3px;
}
.timed-recording-modal .preset-btn.active .cap-tag,
.timed-recording-modal .preset-btn.is-active .cap-tag {
  color: #fff;
  background: rgb(255 255 255 / 18%);
  border-color: rgb(255 255 255 / 30%);
}
</style>
