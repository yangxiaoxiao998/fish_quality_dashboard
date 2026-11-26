<template>
  <div
    class="embedded-video-report"
    :class="{ 'is-embedded': embedded }"
  >
    <div v-if="!videoId" class="empty-tip">
      <a-empty description="请选择视频查看检测报告" />
    </div>

    <div v-else-if="loadError" class="error-tip">
      <a-alert type="error" :show-icon="true">
        <template #title>加载失败</template>
        {{ loadError }}
      </a-alert>
    </div>

    <div v-show="videoId && !loadError">
      <template v-if="embedded">
        <ReportSection id="embedded-text-block" title="一、文字板块">
          <a-descriptions :column="3" size="medium" bordered>
            <a-descriptions-item label="视频编号">
              <code>{{ reportVideoId }}</code>
            </a-descriptions-item>
            <a-descriptions-item label="检测状态">{{ statusText }}</a-descriptions-item>
            <a-descriptions-item label="导入时间">
              {{ formatChinaDateTime(summaryData.created_at) }}
            </a-descriptions-item>
            <a-descriptions-item label="总鱼数">
              {{ formatNumber(summaryData.total_fish) }}
            </a-descriptions-item>
            <a-descriptions-item label="完整鱼数">
              {{ formatNumber(summaryData.complete_fish) }}
            </a-descriptions-item>
            <a-descriptions-item label="不完整鱼数">
              {{ formatNumber(summaryData.incomplete_fish) }}
            </a-descriptions-item>
            <a-descriptions-item label="检测失败鱼数">
              {{ formatNumber(summaryData.failed_fish) }}
            </a-descriptions-item>
            <!-- <a-descriptions-item label="破损鱼数">
              {{ formatNumber(summaryData.damaged_fish) }}
            </a-descriptions-item> -->
            <a-descriptions-item label="平均破损率">
              {{ formatPercent(summaryData.avg_surface_damage_ratio) }}
            </a-descriptions-item>
            <!-- <a-descriptions-item label="最大破损率">
              {{ formatPercent(summaryData.max_surface_damage_ratio) }}
            </a-descriptions-item> -->
          </a-descriptions>
        </ReportSection>

        <ReportSection id="embedded-chart-block" title="二、可视化统计分析">
          <a-empty
            v-if="!hasAnyFish"
            description="暂无鱼体数据"
          />
          <div
            v-else-if="!hasMetricData"
            class="metric-empty-tip"
          >
            <a-alert type="info" :show-icon="true">
              <template #title>暂无可用的五维测量指标</template>
              当前视频的 {{ summaryData.total_fish || 0 }} 条鱼均为不完整鱼（incomplete），
              尚未测量出体长 / 体高 / 体重 / 肥满度 / 体表破损率。
              下方"鱼体检测明细"仍可查看鱼图、序号、状态与帧索引。
            </a-alert>
          </div>
          <div v-else class="charts-grid">
            <div class="chart-card">
              <div class="chart-title">鱼体状态分布</div>
              <div ref="statusDistChartRef" class="chart-canvas chart-canvas-tall"></div>
            </div>
            <div class="chart-card">
              <div class="chart-title">体长分布 (cm)</div>
              <div ref="lengthDistChartRef" class="chart-canvas"></div>
            </div>
             <div class="chart-card">
               <div class="chart-title">无尾体长分布 (cm)</div>
               <div ref="noTailDistChartRef" class="chart-canvas"></div>
             </div>
             <div class="chart-card">
               <div class="chart-title">体高分布 (cm)</div>
               <div ref="heightDistChartRef" class="chart-canvas"></div>
             </div>
            <div class="chart-card">
              <div class="chart-title">体重分布 (g)</div>
              <div ref="weightDistChartRef" class="chart-canvas"></div>
            </div>
            <div class="chart-card">
              <div class="chart-title">肥满度分布</div>
              <div ref="conditionDistChartRef" class="chart-canvas"></div>
            </div>
            <div
              v-if="hasDamageData"
              class="chart-card"
            >
              <div class="chart-title">破损率分布 (×100%)</div>
              <div ref="damageDistChartRef" class="chart-canvas"></div>
            </div>
          </div>
        </ReportSection>

        <ReportSection
          id="embedded-video-block"
          title="三、视频对比展示"
          subtitle="左侧为 Grounded-SAM 检测结果视频，右侧为源视频。"
        >
          <div class="video-compare-grid">
            <div class="video-panel">
              <div class="video-panel-title">Grounded-SAM 检测结果视频</div>
              <video
                 v-if="resultGroundedUrl && !resultVideoErrored"
                class="report-video"
                :src="withMediaVersion(resultGroundedUrl)"
                controls
                autoplay
                muted
                loop
                playsinline
                preload="metadata"
                 @error="onResultVideoError"
               />
               <a-alert
                 v-else-if="resultVideoErrored"
                 type="error"
                 :show-icon="true"
                 style="margin-top: 8px"
               >
                 <template #title>Grounded-SAM 检测结果视频加载失败</template>
                 请检查静态文件是否存在，或确认视频为 H.264 / yuv420p 编码。
               </a-alert>
               <a-empty v-else description="暂无 Grounded-SAM 检测结果视频" />
            </div>

            <div class="video-panel">
              <div class="video-panel-title">源视频</div>
              <video
                     v-if="sourcePlayableUrl && !sourceVideoErrored"
                class="report-video"
                :src="withMediaVersion(sourcePlayableUrl)"
                controls
                autoplay
                muted
                loop
                playsinline
                preload="metadata"
                 @error="onSourceVideoError"
               />
               <a-alert
                 v-else-if="sourceVideoErrored"
                 type="error"
                 :show-icon="true"
                 style="margin-top: 8px"
               >
                 <template #title>源视频加载失败</template>
                 请检查源视频文件是否存在，或确认浏览器支持 H.264 编码。
               </a-alert>
               <a-empty v-else description="暂无可播放源视频" />
            </div>
          </div>
        </ReportSection>

        <ReportSection id="embedded-fish-detail" title="四、鱼体检测明细">
          <div class="fish-toolbar">
            <a-input
              v-model="fishSearchTrackId"
               placeholder="输入序号检索"
              allow-clear
              style="width: 220px"
              @press-enter="loadFishTable"
              @clear="loadFishTable"
            >
              <template #prefix>
                <i class="fa-solid fa-magnifying-glass"></i>
              </template>
            </a-input>
            <a-select
              v-model="fishStatusFilter"
              placeholder="鱼状态（不限）"
              allow-clear
              style="width: 180px"
              @change="loadFishTable"
              @clear="loadFishTable"
            >
              <a-option value="">全部</a-option>
              <a-option value="done">完整鱼</a-option>
              <a-option value="incomplete">不完整鱼</a-option>
              <a-option value="failed">失败鱼</a-option>
            </a-select>
            <a-button type="primary" @click="loadFishTable">查询</a-button>
            <a-button @click="resetFishFilter">重置</a-button>
            <span class="fish-count">共 {{ fishTotal }} 条</span>
          </div>

                              <a-table
                        :data="fishRows"
                        :pagination="false"
                        :loading="fishLoading"
                        row-key="fish_id"
                        stripe
                        hoverable
                        :scroll="{ x: 1600 }"
                      >
                        <template #columns>
                          <a-table-column title="序号" data-index="track_id" :width="90" fixed="left">
                            <template #cell="{ record }">
                              <a-tag color="arcoblue">{{ record.track_id ?? '--' }}</a-tag>
                            </template>
                          </a-table-column>

                          <a-table-column title="鱼图" data-index="crop_image_url" :width="120">
                            <template #cell="{ record }">
                              <div class="fish-thumb-cell">
                                <img
                                  v-if="resultSearchedFishCropUrl(record)"
                                  :src="resultSearchedFishCropUrl(record)"
                                  :alt="'fish-' + (record.track_id ?? record.fish_uid ?? record.fish_id)"
                                  @error="onImgError"
                                />
                                <span v-else class="dim">--</span>
                              </div>
                            </template>
                          </a-table-column>

                          <a-table-column title="状态" data-index="status" :width="110">
                            <template #cell="{ record }">
                              <a-tag :color="statusTagColor(record.status)">
                                {{ statusTagText(record.status) }}
                              </a-tag>
                            </template>
                          </a-table-column>

                           <a-table-column title="体长 (cm)" data-index="body_length_cm" :width="110">
                            <template #cell="{ record }">
                              <span :class="{ dim: isEmptyValue(record.body_length_cm) }">
                                {{ formatCell(record.body_length_cm, 2) }}
                              </span>
                            </template>
                          </a-table-column>

                           <a-table-column title="体高 (cm)" data-index="body_height_cm" :width="110">
                            <template #cell="{ record }">
                              <span :class="{ dim: isEmptyValue(record.body_height_cm) }">
                                {{ formatCell(record.body_height_cm, 2) }}
                              </span>
                            </template>
                          </a-table-column>

                          <a-table-column title="体重 (g)" data-index="pred_weight_g" :width="110">
                            <template #cell="{ record }">
                              <span :class="{ dim: isEmptyValue(record.pred_weight_g) }">
                                {{ formatCell(record.pred_weight_g, 2) }}
                              </span>
                            </template>
                          </a-table-column>

                          <a-table-column title="肥满度" data-index="condition_factor" :width="100">
                            <template #cell="{ record }">
                              <span :class="{ dim: isEmptyValue(record.condition_factor) }">
                                {{ formatCell(record.condition_factor, 3) }}
                              </span>
                            </template>
                          </a-table-column>

                          <a-table-column title="破损率" data-index="surface_damage_ratio" :width="100">
                            <template #cell="{ record }">
                              {{ formatPercentRatio(record.surface_damage_ratio) }}
                            </template>
                          </a-table-column>

                           <a-table-column title="腹部颜色" data-index="hex_color" :width="110">
                             <template #cell="{ record }">
                               <span
                                 v-if="isHexColor(record.hex_color)"
                                 class="color-swatch"
                                 :style="{ backgroundColor: record.hex_color }"
                               />
                               <span v-else class="dim">--</span>
                             </template>
                           </a-table-column>

                           <a-table-column title="弓背比值" data-index="arched_back_ratio" :width="100">
                             <template #cell="{ record }">
                               <span :class="{ dim: isEmptyValue(record.arched_back_ratio) }">
                                 {{ formatCell(record.arched_back_ratio, 3) }}
                               </span>
                             </template>
                           </a-table-column>

                           <a-table-column title="体尺标注图" data-index="fish_condition_image" :width="110">
                             <template #cell="{ record }">
                               <div class="fish-thumb-cell">
                                 <img
                                   v-if="resultSearchedFishConditionImageUrl(record)"
                                   :src="resultSearchedFishConditionImageUrl(record)"
                                   alt="fish-condition"
                                   @error="onImgError"
                                 />
                                 <span v-else class="dim">--</span>
                               </div>
                             </template>
                           </a-table-column>

                           <a-table-column title="弓背标注图" data-index="arched_back_image" :width="110">
                             <template #cell="{ record }">
                               <div class="fish-thumb-cell">
                                 <img
                                   v-if="resultSearchedArchedBackImageUrl(record)"
                                   :src="resultSearchedArchedBackImageUrl(record)"
                                   alt="arched-back"
                                   @error="onImgError"
                                 />
                                 <span v-else class="dim">--</span>
                               </div>
                             </template>
                           </a-table-column>

                           <a-table-column title="操作" data-index="action" :width="100" fixed="right">
                            <template #cell="{ record }">
                              <a-button size="small" type="primary" @click="openFishDetail(record)">
                                查看
                              </a-button>
                            </template>
                          </a-table-column>
                        </template>
                      </a-table>

          <a-pagination
            v-model:current="fishPage"
            v-model:page-size="fishPageSize"
            :total="fishTotal"
            :page-size-options="[10, 20, 50, 100]"
            show-total
            show-page-size
            style="margin-top: 12px; justify-content: flex-end; display: flex"
             @change="handleFishPageChange"
             @page-size-change="handleFishPageSizeChange"
          />
        </ReportSection>

        <ReportSection id="embedded-pdf-report" title="五、查看PDF报表">
          <div class="pdf-report-actions">
            <a-button
              type="primary"
              :loading="videoPdfGenerating"
              @click="regenerateVideoPdfReport"
            >
              重新生成报表
            </a-button>
            <a-button
              v-if="reportPdfUrl"
              :href="withMediaVersion(reportPdfUrl)"
              download
            >
              下载PDF
            </a-button>
            <a-button
              v-if="reportDocxUrl"
              :href="withMediaVersion(reportDocxUrl)"
              download
            >
              下载Word
            </a-button>
          </div>

          <div v-if="reportPdfUrl" class="pdf-preview-wrap">
            <iframe
              class="pdf-preview"
              :src="withMediaVersion(reportPdfUrl)"
              title="PDF报表预览"
            />
          </div>
          <a-empty
            v-else
            description="PDF 报表暂未生成，请等待视频计算完成或重新导入结果后生成。"
          />
        </ReportSection>
      </template>

      <!-- 独立模式：保持原有 4 板块 + 右侧目录 -->
      <template v-else>
        <div class="report-page-layout">
          <main class="report-main">
            <ReportSection id="text-block" title="一、文字板块">
              <a-descriptions :column="3" size="medium" bordered>
                <a-descriptions-item label="视频编号">
                  <code>{{ reportVideoId }}</code>
                </a-descriptions-item>
                <a-descriptions-item label="检测状态">{{ statusText }}</a-descriptions-item>
                <a-descriptions-item label="导入时间">
                  {{ formatChinaDateTime(summaryData.created_at) }}
                </a-descriptions-item>
                <a-descriptions-item label="总鱼数">
                  {{ formatNumber(summaryData.total_fish) }}
                </a-descriptions-item>
                <a-descriptions-item label="完整鱼数">
                  {{ formatNumber(summaryData.complete_fish) }}
                </a-descriptions-item>
                <a-descriptions-item label="不完整鱼数">
                  {{ formatNumber(summaryData.incomplete_fish) }}
                </a-descriptions-item>
                <a-descriptions-item label="检测失败鱼数">
                  {{ formatNumber(summaryData.failed_fish) }}
                </a-descriptions-item>
                <!-- <a-descriptions-item label="破损鱼数">
                  {{ formatNumber(summaryData.damaged_fish) }}
                </a-descriptions-item> -->
                <a-descriptions-item label="平均破损率">
                  {{ formatPercent(summaryData.avg_surface_damage_ratio) }}
                </a-descriptions-item>
                <!-- <a-descriptions-item label="最大破损率">
                  {{ formatPercent(summaryData.max_surface_damage_ratio) }}
                </a-descriptions-item> -->
              </a-descriptions>
            </ReportSection>

            <ReportSection id="chart-block" title="二、可视化统计分析">
              <a-empty v-if="!hasChartData" description="暂无可用于绘图的数据" />
              <div v-else class="charts-grid">
                <div class="chart-card">
                  <div class="chart-title">鱼体状态分布</div>
                  <div ref="statusDistChartRef" class="chart-canvas chart-canvas-tall"></div>
                </div>
                <div class="chart-card">
                    <div class="chart-title">体长分布 (cm)</div>
                  <div ref="lengthDistChartRef" class="chart-canvas"></div>
                </div>
                 <div class="chart-card">
                   <div class="chart-title">无尾体长分布 (cm)</div>
                   <div ref="noTailDistChartRef" class="chart-canvas"></div>
                 </div>
                <div class="chart-card">
                  <div class="chart-title">体高分布 (cm)</div>
                  <div ref="heightDistChartRef" class="chart-canvas"></div>
                </div>
                <div class="chart-card">
                  <div class="chart-title">体重分布 (g)</div>
                  <div ref="weightDistChartRef" class="chart-canvas"></div>
                </div>
                <div class="chart-card">
                  <div class="chart-title">肥满度分布</div>
                  <div ref="conditionDistChartRef" class="chart-canvas"></div>
                </div>
                <div
                  v-if="hasDamageData"
                  class="chart-card"
                >
                  <div class="chart-title">破损率分布 (×100%)</div>
                  <div ref="damageDistChartRef" class="chart-canvas"></div>
                </div>
              </div>
            </ReportSection>

            <ReportSection
              id="video-block"
              title="三、视频对比展示"
              subtitle="左侧为 Grounded-SAM 检测结果视频，右侧为源视频。"
            >
              <div class="video-compare-grid">
                <div class="video-panel">
                  <div class="video-panel-title">Grounded-SAM 检测结果视频</div>
                  <video
                    v-if="resultGroundedUrl && !resultVideoErrored"
                    class="report-video"
                    :src="withMediaVersion(resultGroundedUrl)"
                    controls
                    autoplay
                    muted
                    loop
                    playsinline
                    preload="metadata"
                    @error="onResultVideoError"
                  />
                  <a-alert
                  v-else-if="resultVideoErrored"
                  type="error"
                  :show-icon="true"
                  style="margin-top: 8px"
                >
                  <template #title>Grounded-SAM 检测结果视频加载失败</template>
                  请检查静态路径或编码格式（需 H.264 / yuv420p）。
                </a-alert>
                <a-empty v-else description="暂无 Grounded-SAM 检测结果视频" />
                </div>

                <div class="video-panel">
                  <div class="video-panel-title">源视频</div>
                  <video
                    v-if="sourcePlayableUrl && !sourceVideoErrored"
                    class="report-video"
                    :src="withMediaVersion(sourcePlayableUrl)"
                    controls
                    autoplay
                    muted
                    loop
                    playsinline
                    preload="metadata"
                    @error="onSourceVideoError"
                  />
                  <a-alert
                  v-else-if="sourceVideoErrored"
                  type="error"
                  :show-icon="true"
                  style="margin-top: 8px"
                >
                  <template #title>源视频加载失败</template>
                  请检查静态路径或编码格式。
                </a-alert>
                <a-alert
                  v-else-if="sourceVideoErrored"
                  type="error"
                  :show-icon="true"
                  style="margin-top: 8px"
                >
                  <template #title>源视频无法播放</template>
                  文件存在但浏览器无法解码（需要 H.264 编码）。
                </a-alert>
                <a-empty v-else description="暂无可播放源视频" />
                </div>
              </div>
            </ReportSection>

            <ReportSection id="fish-block" title="四、鱼体检测明细">
              <div class="fish-toolbar">
                <a-input
                  v-model="fishSearchTrackId"
                   placeholder="输入序号检索"
                  allow-clear
                  style="width: 220px"
                  @press-enter="loadFishTable"
                  @clear="loadFishTable"
                >
                  <template #prefix>
                    <i class="fa-solid fa-magnifying-glass"></i>
                  </template>
                </a-input>
                <a-select
                  v-model="fishStatusFilter"
                  placeholder="鱼状态（不限）"
                  allow-clear
                  style="width: 180px"
                  @change="loadFishTable"
                  @clear="loadFishTable"
                >
                  <a-option value="">全部</a-option>
                <a-option value="done">完整鱼</a-option>
                <a-option value="incomplete">不完整鱼</a-option>
                <a-option value="failed">失败鱼</a-option>
                </a-select>
                <a-button type="primary" @click="loadFishTable">查询</a-button>
                <a-button @click="resetFishFilter">重置</a-button>
                <span class="fish-count">共 {{ fishTotal }} 条</span>
              </div>

                            <a-table
                :data="fishRows"
                :pagination="false"
                :loading="fishLoading"
                row-key="fish_id"
                stripe
                hoverable
                :scroll="{ x: 1280 }"
              >
                <template #columns>
                   <a-table-column title="序号" data-index="track_id" :width="90" fixed="left">
                    <template #cell="{ record }">
                      <a-tag color="arcoblue">{{ record.track_id ?? '--' }}</a-tag>
                    </template>
                  </a-table-column>

                  <a-table-column title="鱼图" data-index="crop_image_url" :width="120">
                    <template #cell="{ record }">
                      <div class="fish-thumb-cell">
                        <img
                          v-if="resultSearchedFishCropUrl(record)"
                          :src="resultSearchedFishCropUrl(record)"
                          :alt="'fish-' + (record.track_id ?? record.fish_uid ?? record.fish_id)"
                          @error="onImgError"
                        />
                        <span v-else class="dim">--</span>
                      </div>
                    </template>
                  </a-table-column>

                  <a-table-column title="状态" data-index="status" :width="110">
                    <template #cell="{ record }">
                      <a-tag :color="statusTagColor(record.status)">
                        {{ statusTagText(record.status) }}
                      </a-tag>
                    </template>
                  </a-table-column>

                   <a-table-column title="体长 (cm)" data-index="body_length_cm" :width="110">
                    <template #cell="{ record }">
                      <span :class="{ dim: isEmptyValue(record.body_length_cm) }">
                        {{ formatCell(record.body_length_cm, 2) }}
                      </span>
                    </template>
                  </a-table-column>

                  <a-table-column title="无尾体长 (cm)" data-index="body_length_no_tail_cm" :width="130">
                    <template #cell="{ record }">
                      <span :class="{ dim: isEmptyValue(record.body_length_no_tail_cm) }">
                        {{ formatCell(record.body_length_no_tail_cm, 2) }}
                      </span>
                    </template>
                  </a-table-column>

                  <a-table-column title="体高 (cm)" data-index="body_height_cm" :width="110">
                    <template #cell="{ record }">
                      <span :class="{ dim: isEmptyValue(record.body_height_cm) }">
                        {{ formatCell(record.body_height_cm, 2) }}
                      </span>
                    </template>
                  </a-table-column>

                  <a-table-column title="体重 (g)" data-index="pred_weight_g" :width="110">
                    <template #cell="{ record }">
                      <span :class="{ dim: isEmptyValue(record.pred_weight_g) }">
                        {{ formatCell(record.pred_weight_g, 2) }}
                      </span>
                    </template>
                  </a-table-column>

                  <a-table-column title="肥满度" data-index="condition_factor" :width="100">
                    <template #cell="{ record }">
                      <span :class="{ dim: isEmptyValue(record.condition_factor) }">
                        {{ formatCell(record.condition_factor, 3) }}
                      </span>
                    </template>
                  </a-table-column>

                          <a-table-column title="破损率" data-index="surface_damage_ratio" :width="100">
                    <template #cell="{ record }">
                      {{ formatPercentRatio(record.surface_damage_ratio) }}
                    </template>
                          </a-table-column>

                          <a-table-column title="腹部颜色" data-index="hex_color" :width="110">
                            <template #cell="{ record }">
                              <span
                                v-if="isHexColor(record.hex_color)"
                                class="color-swatch"
                                :style="{ backgroundColor: record.hex_color }"
                              />
                              <span v-else class="dim">--</span>
                            </template>
                          </a-table-column>

                  <a-table-column title="操作" data-index="action" :width="100" fixed="right">
                    <template #cell="{ record }">
                      <a-button size="small" type="primary" @click="openFishDetail(record)">
                        查看
                      </a-button>
                    </template>
                  </a-table-column>
                </template>
              </a-table>

              <a-pagination
                v-model:current="fishPage"
                v-model:page-size="fishPageSize"
                :total="fishTotal"
                :page-size-options="[10, 20, 50, 100]"
                show-total
                show-page-size
                style="margin-top: 12px; justify-content: flex-end; display: flex"
                 @change="handleFishPageChange"
                 @page-size-change="handleFishPageSizeChange"
              />
            </ReportSection>
<ReportSection id="pdf-report" title="五、查看PDF报表">
              <div class="pdf-report-actions">
                <a-button
                  type="primary"
                  :loading="videoPdfGenerating"
                  @click="regenerateVideoPdfReport"
                >
                  重新生成报表
                </a-button>
                <a-button
                  v-if="reportPdfUrl"
                  :href="withMediaVersion(reportPdfUrl)"
                  download
                >
                  下载PDF
                </a-button>
                <a-button
                  v-if="reportDocxUrl"
                  :href="withMediaVersion(reportDocxUrl)"
                  download
                >
                  下载Word
                </a-button>
              </div>

              <div v-if="reportPdfUrl" class="pdf-preview-wrap">
                <iframe
                  class="pdf-preview"
                  :src="withMediaVersion(reportPdfUrl)"
                  title="PDF报表预览"
                />
              </div>
              <a-empty
                v-else
                description="PDF 报表暂未生成，请等待视频计算完成或重新导入结果后生成。"
              />
            </ReportSection>
          </main>

          <aside class="report-aside">
            <ReportToc :items="tocItems" separator="、" />
          </aside>
        </div>
      </template>

      <a-modal
        v-model:visible="fishDetailVisible"
        title="鱼体检测详情"
        :footer="false"
        :mask-closable="true"
        width="min(860px, 92vw)"
        :body-style="{ padding: '12px 16px' }"
      >
        <div v-if="currentFishRecord" class="fish-detail-card">
          <div class="fish-detail-content">
            <div class="fish-detail-image-box">
              <button
                v-if="fishRows.length > 1"
                class="fish-detail-nav fish-detail-nav-left"
                type="button"
                aria-label="查看上一条鱼"
                :disabled="currentFishIndex <= 0"
                @click.stop="showPrevFish"
              >
                &lt;
              </button>

              <img
                v-if="resultSearchedFishCropUrl(currentFishRecord)"
                :src="resultSearchedFishCropUrl(currentFishRecord)"
                :alt="'fish-' + (currentFishRecord.track_id ?? currentFishRecord.fish_uid ?? '')"
              />
              <span v-else class="dim">暂无鱼图</span>

              <button
                v-if="fishRows.length > 1"
                class="fish-detail-nav fish-detail-nav-right"
                type="button"
                aria-label="查看下一条鱼"
                :disabled="currentFishIndex >= fishRows.length - 1"
                @click.stop="showNextFish"
              >
                &gt;
              </button>
            </div>

            <div class="fish-detail-info-grid">
              <div class="info-item">
                <span class="label">视频编号</span>
                <span class="value">{{ reportVideoId || '--' }}</span>
              </div>
              <div class="info-item">
                 <span class="label">序号</span>
                <span class="value">{{ currentFishRecord.track_id ?? '--' }}</span>
              </div>
              <div class="info-item">
                <span class="label">是否完整</span>
                <span class="value">
                  <a-tag :color="statusTagColor(currentFishRecord.status)">
                    {{ statusTagText(currentFishRecord.status) }}
                  </a-tag>
                </span>
              </div>
              <div class="info-item">
                <span class="label">体长</span>
                <span class="value">
                  {{ formatCell(currentFishRecord.body_length_cm, 2) }} cm
                </span>
              </div>
              <div class="info-item">
                   <span class="label">体高</span>
                <span class="value">
                   {{ formatCell(currentFishRecord.body_height_cm, 2) }} cm
                </span>
              </div>
              <div class="info-item">
                <span class="label">体高</span>
                <span class="value">
                  {{ formatCell(currentFishRecord.body_height_cm, 2) }} cm
                </span>
              </div>
              <div class="info-item">
                <span class="label">体重</span>
                <span class="value">
                  {{ formatCell(currentFishRecord.pred_weight_g, 2) }} g
                </span>
              </div>
              <div class="info-item">
                <span class="label">肥满度</span>
                <span class="value">
                  {{ formatCell(currentFishRecord.condition_factor, 3) }}
                </span>
              </div>
              <div class="info-item">
                <span class="label">破损率</span>
                <span class="value">
                  {{ formatPercentRatio(currentFishRecord.surface_damage_ratio) }}
                </span>
              </div>
              <div v-if="currentFishRecord.error_message" class="info-item">
                <span class="label">说明</span>
                <span class="value">{{ currentFishRecord.error_message }}</span>
              </div>
            </div>
          </div>
        </div>

        <a-empty
          v-else
          description="暂无可查看的鱼体记录"
        />
      </a-modal>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { useRoute } from 'vue-router'
import { Message } from '@arco-design/web-vue'
import { http, toErrorMessage } from '@/api/http'
import { normalizeReportUrl } from '@/utils/reportPath'
import ReportSection from '@/components/report/ReportSection.vue'
import ReportToc from '@/components/report/ReportToc.vue'
import { formatChinaDateTime } from '@/utils/time'
import { logUserAction } from '@/utils/operationLogger'
import type { ReportTocItem } from '@/components/report/ReportToc.vue'

interface SummaryData {
  video_id: string
  status: string
  input_video: string
  original_input_video: string
  grounded_video: string
  result_video_url: string
  grounded_video_url: string
  measurement_csv_url: string
  pipeline_result_path: string
  result_dir: string
  total_fish: number | null
  complete_fish: number | null
  incomplete_fish: number | null
  failed_fish: number | null
  damaged_fish: number | null
  avg_surface_damage_ratio: number | null
  max_surface_damage_ratio: number | null
  created_at: string
}

const tocItems: ReportTocItem[] = [
  { id: 'text-block', index: '一', title: '一、文字板块' },
  { id: 'chart-block', index: '二', title: '二、可视化统计分析' },
  { id: 'video-block', index: '三', title: '三、视频对比展示' },
  { id: 'fish-block', index: '四', title: '四、鱼体检测明细' },
  { id: 'pdf-report', index: '五', title: '五、查看PDF报表' },
]  

const props = withDefaults(
  defineProps<{
    videoId: string
    embedded?: boolean
    taskStatus?: string
  }>(),
  { embedded: false, taskStatus: '' }
)

function friendlyReportError(_err: any, status?: string): string {
  const s = String(status || '').toLowerCase()
  if (s === 'failed') {
    return '该视频检测任务执行失败，暂时无法查看完整检测报告。请查看失败原因，或重新上传/重新运行检测。'
  }
  if (s === 'recording') {
    return '该视频仍在录制或处理中，检测报告尚未生成。请等待任务完成后刷新页面查看。'
  }
  if (['queued', 'processing', 'running'].includes(s)) {
    return '该视频正在处理中，检测报告尚未生成。请稍后刷新。'
  }
  return '该视频报告暂未生成，请确认视频检测任务已完成。'
}


const route = useRoute()

const loading = ref(false)
const loadError = ref<string>('')
const reportVideoId = ref<string>('')
const summaryData = ref<SummaryData>(emptySummary())
const sourcePlayableUrl = ref<string>('')
const resultGroundedUrl = ref<string>('')
const resultVideoErrored = ref<boolean>(false)
const sourceVideoErrored = ref<boolean>(false)
const resultData = ref<any>({})

// 视频 task 信息（来自 video task 接口）
const currentTask = ref<any>(null)

const statusDistChartRef = ref<HTMLElement>()
const lengthDistChartRef = ref<HTMLElement>()
const noTailDistChartRef = ref<HTMLElement>()
const heightDistChartRef = ref<HTMLElement>()
const weightDistChartRef = ref<HTMLElement>()
const conditionDistChartRef = ref<HTMLElement>()
const damageDistChartRef = ref<HTMLElement>()

let charts: echarts.ECharts[] = []
let chartResizeObserver: ResizeObserver | null = null

const reportStatus = computed(() => summaryData.value.status || '')
const statusText = computed(() => statusToText(reportStatus.value))
const hasChartData = computed(() => (summaryData.value.total_fish || 0) > 0)
const hasDamageData = computed(() => (summaryData.value.damaged_fish || 0) > 0)

// 是否有任何 fish 行（用于在 metrics 全空时决定给出友好提示而不是空白页）。
const hasAnyFish = computed(() => (allFishRows.value || []).length > 0)

// 是否至少存在一条鱼具有体长或体高或体重等五维指标。
const hasMetricData = computed(() => {
  const rows = allFishRows.value || []
  return rows.some((f) => {
    const v = (k: string) => {
      const x = (f as any)?.[k]
      if (x === null || x === undefined || x === '') return false
      const n = Number(x)
      return Number.isFinite(n)
    }
    return (
      v('body_length_cm') ||
      v('body_length_no_tail_cm') ||
      v('body_height_cm') ||
      v('pred_weight_g') ||
      v('condition_factor') ||
      v('surface_damage_ratio')
    )
  })
})

const allFishRows = ref<any[]>([])
const metricFishRows = computed(() =>
  allFishRows.value.filter((fish) => {
    const status = String(fish?.status || '').toLowerCase()
    if (status !== 'complete' && status !== 'done') return false
    return ['body_length_cm', 'body_height_cm', 'pred_weight_g', 'condition_factor', 'surface_damage_ratio']
      .every((key) => Number.isFinite(Number(fish?.[key])))
  }),
)
// 避免浏览器缓存旧视频 / 旧文件
const mediaVersion = ref<number>(Date.now())
function withMediaVersion(url: string): string {
  if (!url) return ''
  if (url.startsWith('blob:')) return url
  const sep = url.includes('?') ? '&' : '?'
  return `${url}${sep}v=${mediaVersion.value}`
}

// 报表 URL：直接从 resultData 中读取后端返回的 *_url 字段
const reportPdfUrl = computed(() =>
  normalizeReportUrl(
    (resultData.value as any)?.report_pdf_url ||
      (resultData.value as any)?.report_pdf ||
      '',
  ),
)
const reportDocxUrl = computed(() =>
  normalizeReportUrl(
    (resultData.value as any)?.report_docx_url ||
      (resultData.value as any)?.report_docx ||
      '',
  ),
)
const videoPdfGenerating = ref<boolean>(false)
const fishRows = ref<any[]>([])
const fishTotal = ref<number>(0)
const fishPage = ref<number>(1)
const fishPageSize = ref<number>(20)
const fishLoading = ref<boolean>(false)
const fishStatusFilter = ref<string>('')
const fishSearchTrackId = ref<string>('')

function reportStaticUrl(path: unknown): string {
  const value = String(path || '').trim()
  if (!value) return ''
  if (value.startsWith('/static/')) return value
  return `/static/${value.replace(/^data\//, '')}`
}

function withReportVersion(url: string): string {
  if (!url) return ''
  const separator = url.includes('?') ? '&' : '?'
  return `${url}${separator}_v=${mediaVersion.value}`
}

async function resolveExistingMediaUrl(url: string): Promise<string> {
  if (!url) return ''
  try {
    await http.head(url, { timeout: 5000 })
    return url
  } catch {
    return ''
  }
}

async function regenerateVideoPdfReport() {
  if (!reportVideoId.value || videoPdfGenerating.value) return
  videoPdfGenerating.value = true
  logUserAction({
    module: '数据报表',
    action: 'regenerate_video_report',
    targetType: 'video',
    targetId: reportVideoId.value,
    message: '重新生成视频报表',
  })
  try {
    const resp = await http.post(
      '/api/reports/fish-quality/video-pdf/generate',
      { video_id: reportVideoId.value },
      { timeout: 180000 },
    )
    const data = resp.data || {}
    const reportResult = data.report_result || {}
    const pdfUrl = reportStaticUrl(
      data.report_pdf_url || data.report_pdf || reportResult.report_pdf || data.file_path,
    )
    const docxUrl = reportStaticUrl(
      data.report_docx_url || data.report_docx || reportResult.report_docx,
    )
    resultData.value = {
      ...resultData.value,
       report_pdf_url: withReportVersion(pdfUrl),
       report_docx_url: withReportVersion(docxUrl),
    }
    mediaVersion.value = Date.now()
    Message.success('视频报表已重新生成')
  } catch (err) {
    Message.error(`重新生成报表失败：${toErrorMessage(err)}`)
  } finally {
    videoPdfGenerating.value = false
  }
}

const fishDetailVisible = ref<boolean>(false)
const currentFishIndex = ref<number>(-1)
const currentFishRecord = computed(() => {
  if (currentFishIndex.value < 0) return null
  return fishRows.value[currentFishIndex.value] || null
})

function fishRecordKey(rec: any): string {
  return String(rec?.fish_id || rec?.fish_uid || rec?.track_id || '')
}

function openFishDetail(record: any) {
  const target = fishRecordKey(record)
  const idx = fishRows.value.findIndex(
    (r: any) => fishRecordKey(r) === target,
  )
  currentFishIndex.value = idx >= 0 ? idx : 0
  logUserAction({
    module: '鱼体数据',
    action: 'view_fish_detail',
    targetType: 'fish',
    targetId: target,
    message: '查看鱼体详情',
  })
  fishDetailVisible.value = true
}

function showPrevFish() {
  if (currentFishIndex.value > 0) {
    currentFishIndex.value -= 1
  }
}

function showNextFish() {
  if (currentFishIndex.value < fishRows.value.length - 1) {
    currentFishIndex.value += 1
  }
}

const fishColumns = [
   { title: '序号', dataIndex: 'track_id', width: 90, fixed: 'left' as const },
  { title: '鱼图', dataIndex: 'crop_image', width: 110 },
  { title: '状态', dataIndex: 'status', width: 100 },
   { title: '体长 (cm)', dataIndex: 'body_length_cm', width: 100 },
   { title: '无尾体长 (cm)', dataIndex: 'body_length_no_tail_cm', width: 120 },
  { title: '体高 (cm)', dataIndex: 'body_height_cm', width: 100 },
  { title: '体重 (g)', dataIndex: 'pred_weight_g', width: 100 },
  { title: '肥满度', dataIndex: 'condition_factor', width: 90 },
  { title: '破损率', dataIndex: 'surface_damage_ratio', width: 90 },
  { title: '操作', dataIndex: 'action', width: 100, fixed: 'right' as const },
]  

function emptySummary(): SummaryData {
  return {
    video_id: '',
    status: '',
    input_video: '',
    original_input_video: '',
    grounded_video: '',
    result_video_url: '',
    grounded_video_url: '',
    measurement_csv_url: '',
    pipeline_result_path: '',
    result_dir: '',
    total_fish: null,
    complete_fish: null,
    incomplete_fish: null,
    failed_fish: null,
    damaged_fish: null,
    avg_surface_damage_ratio: null,
    max_surface_damage_ratio: null,
    created_at: '',
  }
}

function statusToText(s: string): string {
  switch ((s || '').toLowerCase()) {
    case 'done':
      return '已完成'
    case 'failed':
      return '失败'
    case 'interrupted':
      return '中断'
    case 'processing':
      return '处理中'
    case 'queued':
      return '排队'
    case 'recording':
      return '录制中'
    case 'no_fish_detected':
      return '未识别到鱼'
    default:
      return s || '--'
  }
}

function formatNumber(v: any): string {
  if (v === null || v === undefined) return '--'
  const n = Number(v)
  if (!Number.isFinite(n)) return '--'
  return String(n)
}

function formatPercent(v: any): string {
  if (v === null || v === undefined) return '--'
  const n = Number(v)
  if (!Number.isFinite(n)) return '--'
  return `${(n * 100).toFixed(2)}%`
}

function formatPercentRatio(v: any): string {
  if (v === null || v === undefined) return '--'
  const n = Number(v)
  if (!Number.isFinite(n)) return '--'
  return `${(n * 100).toFixed(2)}%`
}

function isEmptyValue(v: any): boolean {
  return v === null || v === undefined || v === ''
}

function formatCell(v: any, decimals: number = 2): string {
  if (v === null || v === undefined || v === '') return '--'
  if (typeof v === 'number') {
    if (!Number.isFinite(v)) return '--'
    return v.toFixed(decimals)
  }
  return String(v)
}

function statusTagColor(s: string): string {
  const v = String(s || '').toLowerCase()
  if (v === 'done' || v === 'complete') return 'green'
  if (v === 'incomplete') return 'orange'
  if (v === 'failed') return 'red'
  return 'gray'
}

function statusTagText(s: string): string {
  const v = String(s || '').toLowerCase()
  if (v === 'done' || v === 'complete') return '完整'
  if (v === 'incomplete') return '不完整'
  if (v === 'failed') return '失败'
  return s || '--'
}

function isHexColor(value: unknown): boolean {
  return /^#[0-9a-f]{6}$/i.test(String(value || '').trim())
}

function onImgError(e: Event) {
  const img = e.target as HTMLImageElement
  console.warn('[EmbeddedVideoReport] fish image load failed:', img.src)
  img.style.opacity = '0.15'
  img.src = 'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjQiIGhlaWdodD0iMjQiIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cmVjdCB3aWR0aD0iMjQiIGhlaWdodD0iMjQiIHg9IjIiIHk9IjIiIGZpbGw9IiMzMzMiLz48dGV4dCB4PSI1IiB5PSIxNSIgZm9udC1mYW1pbHk9IkFyaWFsIiBmb250LXNpemU9IjE0IiBmaWxsPSIjZmZmIj57fTwvdGV4dD48L3N2Zz4='
}

async function loadWebmFallback(kind: 'source' | 'result'): Promise<void> {
  const videoId = reportVideoId.value
  if (!videoId) return
  try {
    const query = kind === 'result' ? '?result_video=true' : ''
    const response = await http.get(`/api/videos/${encodeURIComponent(videoId)}/webm${query}`)
    if (response.status === 202 || response.data?.processing) {
      window.setTimeout(() => void loadWebmFallback(kind), 3000)
      return
    }
    const url = normalizeReportUrl(response.data?.webm_url || '')
    if (!url) throw new Error('WebM URL 为空')
    if (kind === 'source') {
      if (sourcePlayableUrl.value === url) return
      sourcePlayableUrl.value = url
      sourceVideoErrored.value = false
    } else {
      if (resultGroundedUrl.value === url) return
      resultGroundedUrl.value = url
      resultVideoErrored.value = false
    }
  } catch (err) {
    console.warn(`[EmbeddedVideoReport] ${kind} WebM fallback failed:`, err)
  }
}

function onSourceVideoError() {
  sourceVideoErrored.value = true
  console.warn('[EmbeddedVideoReport] source video decode failed:', sourcePlayableUrl.value)
  if (!sourcePlayableUrl.value.endsWith('.webm')) void loadWebmFallback('source')
}

watch(sourcePlayableUrl, () => {
  sourceVideoErrored.value = false
})

function onResultVideoError() {
  resultVideoErrored.value = true
  if (!resultGroundedUrl.value.endsWith('.webm')) void loadWebmFallback('result')
}

function resultSearchedFishCropUrl(fish: any): string {
  if (!fish) return ''
  const picked =
    fish.crop_image_url ||
    fish.crop_image_path ||
    fish.corrected_image_url ||
    fish.corrected_image_path ||
    fish.image_url ||
    fish.image_path ||
    ''
  const url = normalizeReportUrl(picked)

  if (typeof window !== 'undefined' && !(window as any).__embeddedFishImgSlotLog) {
    console.log('[EmbeddedVideoReport] fish image slot executed:', {
      track_id: fish?.track_id,
      fish_uid: fish?.fish_uid,
      crop_image_url: fish?.crop_image_url,
      crop_image_path: fish?.crop_image_path,
      picked,
      normalized: url,
    })
    ;(window as any).__embeddedFishImgSlotLog = true
  }

  return url
}

function resultSearchedFishConditionImageUrl(fish: any): string {
  if (!fish) return ''
  const picked = fish.fish_condition_image_path || fish.fish_condition_image_url || ''
  return normalizeReportUrl(picked)
}

function resultSearchedArchedBackImageUrl(fish: any): string {
  if (!fish) return ''
  const picked = fish.arched_back_image_path || fish.arched_back_image_url || ''
  return normalizeReportUrl(picked)
}

function loadFishTable() {
  // 查询条件变化时回到第一页；页码切换走 handleFishPageChange。
  fishPage.value = 1
  applyFishFilterAndPage()
}

function getFilteredFishRows(): any[] {
  const trackKeyword = String(fishSearchTrackId.value || '').trim()
  const status = String(fishStatusFilter.value || '').trim().toLowerCase()
  return allFishRows.value.filter((fish) => {
    // 仅按 track_id 精确匹配；fish_uid 不参与模糊匹配（fish_uid 中包含日期数字，会误命中）
    const trackId = String(fish?.track_id ?? fish?.id ?? '').trim()
    const rawFishStatus = String(fish?.status || '').toLowerCase()
    const fishStatus = rawFishStatus === 'complete' ? 'done' : rawFishStatus
    if (trackKeyword) {
      if (trackId !== trackKeyword) return false
    }
    if (status && fishStatus !== status) {
      return false
    }
    return true
  })
}

function applyFishFilterAndPage() {
  const filtered = getFilteredFishRows()
  fishTotal.value = filtered.length
  const start = (fishPage.value - 1) * fishPageSize.value
  const end = start + fishPageSize.value
  fishRows.value = filtered.slice(start, end)
}

function handleFishPageChange(page: number) {
  fishPage.value = page
  applyFishFilterAndPage()
  requestAnimationFrame(() => {
    document.getElementById(props.embedded ? 'embedded-fish-detail' : 'fish-block')?.scrollIntoView({
      behavior: 'smooth',
      block: 'start',
    })
  })
}

function handleFishPageSizeChange(size: number) {
  fishPageSize.value = size
  fishPage.value = 1
  applyFishFilterAndPage()
  requestAnimationFrame(() => {
    document.getElementById(props.embedded ? 'embedded-fish-detail' : 'fish-block')?.scrollIntoView({
      behavior: 'smooth',
      block: 'start',
    })
  })
}

function resetFishFilter() {
  fishStatusFilter.value = ''
  fishSearchTrackId.value = ''
  fishPage.value = 1
  applyFishFilterAndPage()
}

async function loadAllData() {
  if (!reportVideoId.value) return
  const taskStatus = String(props.taskStatus || '').toLowerCase()
  if (taskStatus && taskStatus !== 'done') {
    resultData.value = null
    allFishRows.value = []
    fishRows.value = []
    fishTotal.value = 0
    summaryData.value = { ...summaryData.value, status: taskStatus }
    loadError.value = friendlyReportError(null, taskStatus)
    loading.value = false
    return
  }
  loading.value = true
  loadError.value = ''
  try {
    const algoResp = await http.get(
      `/api/algorithm/videos/${encodeURIComponent(reportVideoId.value)}/result`
    )
    const payload = algoResp.data?.data || algoResp.data || {}
    resultData.value = payload || {}
    mergeSummaryFromAlgo(payload)

    const fishes = payload.fish || payload.fishes || []
    allFishRows.value = Array.isArray(fishes) ? fishes : []
    fishTotal.value = 0

    try {
      const taskResp = await http.get('/api/videos', {
        params: { keyword: reportVideoId.value, page: 1, page_size: 1 },
      })
      const t = (taskResp.data?.items || []).find(
        (v: any) => v?.video_id === reportVideoId.value
      )
      if (t) {
        summaryData.value.status = t.status || summaryData.value.status
        summaryData.value.created_at = t.created_at || summaryData.value.created_at
        if (summaryData.value.total_fish === null) {
          summaryData.value.total_fish = t.total_fish ?? null
        }
        if (summaryData.value.complete_fish === null) {
          summaryData.value.complete_fish = t.complete_fish ?? null
        }
        if (summaryData.value.incomplete_fish === null) {
          summaryData.value.incomplete_fish = t.incomplete_fish ?? null
        }
        if (summaryData.value.failed_fish === null) {
          summaryData.value.failed_fish = t.failed_fish ?? null
        }
        if (summaryData.value.damaged_fish === null) {
          summaryData.value.damaged_fish = t.damaged_fish ?? null
        }
        if (summaryData.value.avg_surface_damage_ratio === null) {
          summaryData.value.avg_surface_damage_ratio =
            t.avg_surface_damage_ratio ?? null
        }
        if (summaryData.value.max_surface_damage_ratio === null) {
          summaryData.value.max_surface_damage_ratio =
            t.max_surface_damage_ratio ?? null
        }
        if (!summaryData.value.input_video && t.input_video) {
          summaryData.value.input_video = t.input_video
        }
        if (!summaryData.value.original_input_video && t.original_input_video) {
          summaryData.value.original_input_video = t.original_input_video
        }
        if (!summaryData.value.grounded_video && t.grounded_video_path) {
          summaryData.value.grounded_video = t.grounded_video_path
        }
        if (!summaryData.value.grounded_video_url && t.grounded_video_url) {
          summaryData.value.grounded_video_url = t.grounded_video_url
        }
      }
    } catch (_) {
      /* tolerate */
    }

    try {
      const playbackResp = await http.get(
        `/api/videos/${encodeURIComponent(reportVideoId.value)}/playback`,
      )
      const playback = playbackResp.data || {}
      sourcePlayableUrl.value = normalizeReportUrl(
        playback.playable_url || playback.source_url || '',
      )
    } catch (_) {
      const fallback = reportVideoId.value
        ? `/static/uploads/videos/playable/${reportVideoId.value}_h264.mp4`
        : ''
      sourcePlayableUrl.value = await resolveExistingMediaUrl(fallback)
    }
    resultGroundedUrl.value = await resolveResultGroundedUrl()
    resultVideoErrored.value = false

    fishPage.value = 1
    applyFishFilterAndPage()

    await nextTick()
    renderCharts()
  } catch (err: any) {
    const raw = toErrorMessage(err) || ''
    if (raw.includes('Internal Server Error') || raw.includes('500')) {
      const ts = String(props.taskStatus || summaryData.value?.status || '').toLowerCase()
      loadError.value = friendlyReportError(err, ts)
    } else {
      loadError.value = raw
    }
  } finally {
    loading.value = false
  }
}

watch(reportVideoId, () => {
  mediaVersion.value = Date.now()
})

function mergeSummaryFromAlgo(payload: any) {
  const data = payload || {}
  summaryData.value = {
    ...emptySummary(),
    video_id: data.video_id || reportVideoId.value,
    status: data.status || '',
    input_video: data.input_video || '',
    original_input_video: data.original_input_video || '',
    grounded_video: data.grounded_video || '',
    result_video_url: data.result_video_url || '',
    grounded_video_url: data.grounded_video_url || '',
    measurement_csv_url: data.measurement_csv_url || '',
    pipeline_result_path: data.pipeline_result_path || '',
    result_dir: data.result_dir || '',
    total_fish: data.summary?.total_fish ?? null,
    complete_fish: data.summary?.complete_fish ?? null,
    incomplete_fish: data.summary?.incomplete_fish ?? 0,
    failed_fish: data.summary?.failed_fish ?? null,
    damaged_fish: data.summary?.damaged_fish ?? null,
    avg_surface_damage_ratio:
      data.summary?.avg_surface_damage_ratio ?? null,
    max_surface_damage_ratio:
      data.summary?.max_surface_damage_ratio ?? null,
    created_at: '',
  }
}

function pickSourceUrl(): string {
  // 源视频 URL 优先级（按 SummaryData 实际字段）：
  //   1. result.input_video
  //   2. result.original_input_video
  //   3. result.video?.playback_url
  //   4. result.video?.video_url
  //   5. result.video?.source_url
  //   6. result.video?.input_video
  //   7. 无可用地址时返回空，不使用已清理的 results/input 副本
  const candidates: any[] = [
    summaryData.value.input_video,
    summaryData.value.original_input_video,
  ]
  // task 信息（video / playback_url / source_url）
  if (currentTask) {
    candidates.push(
      currentTask.value?.playback_url,
      currentTask.value?.video_url,
      currentTask.value?.source_url,
      currentTask.value?.input_video,
    )
  }
  for (const c of candidates) {
    const u = normalizeReportUrl(c)
    if (u && !u.includes('/results/') && !u.includes('/input/')) return u
  }
  return reportVideoId.value
    ? `/static/uploads/videos/playable/${reportVideoId.value}_h264.mp4`
    : ''
}

async function resolveResultGroundedUrl(): Promise<string> {
  // Grounded-SAM 视频 URL 优先级：
  //   1. result.grounded_video_url
  //   2. result.grounded_video
  //   3. task.grounded_video_url / grounded_video_path
  //   4. /static/results/<video_id>/videos/grounded_sam.mp4 兜底
  const candidates: any[] = [
    summaryData.value.result_video_url,
    summaryData.value.grounded_video_url,
    summaryData.value.grounded_video,
  ]
  if (currentTask) {
    candidates.push(
      currentTask.value?.grounded_video_url,
      currentTask.value?.grounded_video_path,
    )
  }
  const fallback = reportVideoId.value
    ? `/static/results/${reportVideoId.value}/videos/grounded_sam.mp4`
    : ''
  for (const c of candidates) {
    const u = normalizeReportUrl(c)
    if (u) return u
  }
  return resolveExistingMediaUrl(fallback)
}

function disposeCharts() {
  chartResizeObserver?.disconnect()
  chartResizeObserver = null
  charts.forEach((c) => c.dispose())
  charts = []
}

function observeChartResize() {
  if (typeof ResizeObserver === 'undefined') return
  const refs = [
    statusDistChartRef,
    lengthDistChartRef,
    noTailDistChartRef,
    heightDistChartRef,
    weightDistChartRef,
    conditionDistChartRef,
    damageDistChartRef,
  ]
  chartResizeObserver = new ResizeObserver(() => {
    charts.forEach((chart) => chart.resize())
  })
  refs.forEach((chartRef) => {
    if (chartRef.value) chartResizeObserver?.observe(chartRef.value)
  })
}

function renderCharts() {
  disposeCharts()
  if (!hasChartData.value) return

  if (statusDistChartRef.value) {
    const c = echarts.init(statusDistChartRef.value)
    const fishes = allFishRows.value
    const statusData = [
      { name: '完整鱼', value: fishes.filter((f) => ['done', 'complete'].includes((f?.status || '').toLowerCase())).length },
      { name: '不完整鱼', value: fishes.filter((f) => (f?.status || '').toLowerCase() === 'incomplete').length },
    ].filter((x) => x.value > 0)
    const total = statusData.reduce((sum, x) => sum + Number(x.value || 0), 0)
    c.setOption({
      tooltip: {
        trigger: 'item',
        formatter: (p: any) => {
          const v = Number(p.value || 0)
          const pct = total > 0 ? (v * 100) / total : 0
          return `${p.name}<br/>数量：${v} 条<br/>占比：${pct.toFixed(2)}%`
        },
      },
       legend: {
         orient: 'vertical',
         right: 4,
         top: 'middle',
         itemGap: 12,
         textStyle: { color: '#4e5969', fontSize: 12 },
       },
       series: [
         {
           type: 'pie',
           center: ['34%', '50%'],
           radius: ['38%', '70%'],
           data: statusData,
           label: {
            formatter: (p: any) => {
              const v = Number(p.value || 0)
              const pct = total > 0 ? (v * 100) / total : 0
              return `${p.name} ${pct.toFixed(1)}%`
             },
           },
           labelLine: { length: 8, length2: 6 },
         },
       ],
    })
    charts.push(c)
  }
  if (lengthDistChartRef.value) {
    const c = echarts.init(lengthDistChartRef.value)
    c.setOption(buildBarOption(
       buildBarBins(metricFishRows.value, (f: any) => {
      const v = f?.body_length_cm
        return v === null || v === undefined ? null : Number(v)
      }, 5, 0, 60),
      '',
       '体长区间（cm）',
      ' cm'
    ))
    charts.push(c)
  }
  if (noTailDistChartRef.value) {
    const c = echarts.init(noTailDistChartRef.value)
    c.setOption(buildBarOption(
       buildBarBins(metricFishRows.value, (f: any) => {
       const v = f?.body_length_no_tail_cm ?? f?.body_length_without_tail
        return v === null || v === undefined ? null : Number(v)
      }, 5, 0, 60),
      '',
       '无尾体长区间（cm）',
      ' cm'
    ))
    charts.push(c)
  }
  if (heightDistChartRef.value) {
    const c = echarts.init(heightDistChartRef.value)
    c.setOption(buildBarOption(
       buildBarBins(metricFishRows.value, (f: any) => {
        const v = f?.body_height_cm
        return v === null || v === undefined ? null : Number(v)
      }, 1, 0, 15),
      '',
      '体高区间（cm）',
      ' cm'
    ))
    charts.push(c)
  }
  if (weightDistChartRef.value) {
    const weightValues = metricFishRows.value
      .map((f: any) => Number(f?.pred_weight_g))
      .filter((v) => Number.isFinite(v) && v > 0)
    if (weightValues.length > 0) {
      const c = echarts.init(weightDistChartRef.value)
      const weightMax = Math.max(100, Math.ceil(Math.max(...weightValues) / 100) * 100)
      c.setOption(buildBarOption(
         buildBarBins(metricFishRows.value, (f: any) => {
          const v = f?.pred_weight_g
          return v === null || v === undefined ? null : Number(v)
        }, 100, 0, weightMax),
        '',
        '体重区间（g）',
        ' g'
      ))
      charts.push(c)
    }
  }
  if (conditionDistChartRef.value) {
    const c = echarts.init(conditionDistChartRef.value)
    const conditionValues = metricFishRows.value
      .map((f: any) => Number(f?.condition_factor))
      .filter((v) => Number.isFinite(v))
    const conditionMax = Math.max(2.5, Math.ceil(Math.max(...conditionValues, 0) * 10) / 10)
    c.setOption(buildBarOption(
      buildBarBins(metricFishRows.value, (f: any) => {
        const v = f?.condition_factor
        return v === null || v === undefined ? null : Number(v)
      }, 0.1, 0, conditionMax),
      '',
      '肥满度区间',
      ''
    ))
    charts.push(c)
  }
  if (hasDamageData.value && damageDistChartRef.value) {
    const c = echarts.init(damageDistChartRef.value)
    const damageBins = buildBarBins(
      metricFishRows.value,
      (f: any) => {
        const v = f?.surface_damage_ratio
        return v === null || v === undefined ? null : Number(v) * 100
      },
      5,
      0,
      50
    ).filter((b: BarBin) => b.count > 0)
    c.setOption(buildBarOption(
      damageBins,
      '',
      '损伤比例区间（%）',
      '%'
    ))
    charts.push(c)
  }
  observeChartResize()
}

interface BarBin {
  label: string
  count: number
}

function buildBarBins(
  data: any[],
  extractor: (f: any) => number | null | undefined,
  binSize: number,
  min: number,
  max: number,
): BarBin[] {
  const bins: BarBin[] = []
  for (let lo = min; lo < max; lo += binSize) {
    const hi = lo + binSize
    const label = `${lo.toFixed(1)}-${hi.toFixed(1)}`
    const count = data.filter((f) => {
      const v = extractor(f)
      if (v === null || v === undefined) return false
      return Number(v) >= lo && Number(v) < hi
    }).length
    bins.push({ label, count })
  }
  return bins
}

function buildBarOption(
  bins: BarBin[],
  title: string,
  xAxisName: string,
  xUnitLabel: string,
): any {
  bins = bins.filter((b) => b.count > 0)
  const counts = bins.map((b) => Number(b.count) || 0)
  const total = counts.reduce((s, v) => s + v, 0)
  const percents = total > 0
    ? counts.map((v) => Math.round((v * 10000) / total) / 100)
    : counts.map(() => 0)
  return {
    tooltip: {
      trigger: 'axis',
      formatter: (params: any) => {
        const arr = Array.isArray(params) ? params : [params]
        const p = arr[0]
        if (!p || p.dataIndex == null) return ''
        const idx = p.dataIndex
        const datum = bins[idx]
        if (!datum) return ''
        const pct = Number(p.value ?? 0)
        return [
          `${xAxisName}：${datum.label}${xUnitLabel}`,
          `占比：${pct.toFixed(2)}%`,
          `数量：${datum.count} 条`,
        ].join('<br/>')
      },
    },
    grid: {
      left: 56,
      right: 12,
      top: 24,
      bottom: bins.length > 8 ? 76 : 58,
      containLabel: true,
    },
    title: title
      ? { text: title, left: 'center', top: 4, textStyle: { fontSize: 13, color: '#1d2129' } }
      : undefined,
    xAxis: {
      type: 'category',
      name: xAxisName,
      nameLocation: 'middle',
      nameGap: 42,
      data: bins.map((b) => b.label),
      axisLabel: {
        interval: bins.length > 8 ? 1 : 0,
        rotate: bins.length > 8 ? 35 : 0,
        hideOverlap: true,
        margin: 10,
        fontSize: 10,
      },
      nameTextStyle: { color: '#4e5969', fontSize: 11 },
    },
    yAxis: {
      type: 'value',
      name: '占比（%）',
      nameLocation: 'middle',
      nameGap: 46,
      min: 0,
      max: 100,
      axisLabel: {
        formatter: (value: number) => (Number(value) === 0 ? '' : `${value}%`),
        color: '#4e5969',
        fontSize: 11,
      },
      splitLine: { lineStyle: { color: '#e5e6eb' } },
      nameTextStyle: { color: '#4e5969', fontSize: 11 },
    },
    series: [
      {
        type: 'bar',
        name: title || xAxisName,
        data: percents,
        barMaxWidth: 36,
        label: {
          show: percents.some((v) => v >= 4),
          formatter: (p: any) => `${Number(p.value || 0).toFixed(1)}%`,
          color: '#1d2129',
          fontSize: 11,
          position: 'top',
        },
        itemStyle: { color: '#2f7bff' },
      },
    ],
  }
}

watch(
  () => props.videoId,
  (val) => {
    const id = String(val || '').trim()
    if (id === reportVideoId.value) return
    reportVideoId.value = id
    if (!id) {
      summaryData.value = emptySummary()
      sourcePlayableUrl.value = ''
      resultGroundedUrl.value = ''
      fishRows.value = []
      fishTotal.value = 0
      resultData.value = {}
      return
    }
    loadAllData()
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  disposeCharts()
})
</script>

<style scoped>
.embedded-video-report {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.embedded-video-report.is-embedded {
  background: transparent;
}
.empty-tip,
.error-tip {
  background: #fff;
  border-radius: 8px;
  padding: 32px;
}
.charts-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  min-width: 0;
  justify-items: stretch;
}
.chart-card {
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  align-items: center;
  background: #fafbfc;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  padding: 8px 10px 10px;
}
.charts-grid > .chart-card:first-child {
  grid-column: 1 / -1;
}
.chart-title {
  width: 100%;
  margin-bottom: 4px;
  font-size: 13px;
  font-weight: 600;
  color: #4e5969;
  text-align: center;
}
.chart-canvas {
  width: min(100%, 520px);
  height: 240px;
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: center;
}
.chart-canvas-tall {
  height: 280px;
}
.video-compare-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
}
.video-panel {
  box-sizing: border-box;
  min-width: 0;
  padding: 12px;
  background: #fff;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
}
.video-panel-title {
  margin-bottom: 10px;
  font-size: 14px;
  font-weight: 600;
  color: #1d2129;
}
.report-video {
  display: block;
  width: 100%;
  max-height: 360px;
  background: #000;
  border-radius: 6px;
}
.fish-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  margin-bottom: 12px;
}
.fish-count {
  margin-left: auto;
  color: #4e5969;
  font-size: 13px;
}
.fish-thumb-cell {
  width: 88px;
  height: 56px;
  background: #000;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}
.fish-thumb-cell img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}

@media (width <= 760px) {
  .charts-grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .charts-grid > .chart-card:first-child {
    grid-column: auto;
  }
}
.dim {
  color: #c9cdd4;
}
.color-swatch {
  display: inline-block;
  width: 58px;
  height: 22px;
  border: 1px solid rgb(0 0 0 / 18%);
  border-radius: 4px;
  vertical-align: middle;
}
.report-page-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 220px;
  gap: 16px;
  align-items: start;
  width: 100%;
  box-sizing: border-box;
  padding: 16px;
  background: #f5f7fb;
}
.report-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.report-aside {
  position: sticky;
  top: 76px;
  align-self: start;
  width: 220px;
  min-width: 220px;
  max-height: calc(100vh - 92px);
  overflow-y: auto;
}

@media (width <= 1200px) {
  .video-compare-grid {
    grid-template-columns: 1fr;
  }
  .report-page-layout {
    grid-template-columns: 1fr;
  }
  .report-aside {
    display: none;
  }
}
.pdf-report-actions {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.metric-empty-tip {
  padding: 16px 4px;
}
.pdf-preview-wrap {
  width: 100%;
  height: 760px;
  background: #f7f8fa;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  overflow: hidden;
}
.pdf-preview {
  width: 100%;
  height: 100%;
  border: 0;
  background: #fff;
}
.fish-detail-card {
  position: relative;
  width: 100%;
  min-height: 560px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.fish-detail-content {
  width: 100%;
  display: grid;
  grid-template-columns: 360px minmax(0, 1fr);
  gap: 20px;
  align-items: stretch;
}
.fish-detail-image-box {
  position: relative;
  width: 100%;
  height: 360px;
  border-radius: 12px;
  background: #061226;
  border: 1px solid rgb(47, 123, 255, 35%);
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}
.fish-detail-image-box img {
  display: block;
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}
.fish-detail-info-grid {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.fish-detail-card .info-item {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid #e5e6eb;
  border-radius: 8px;
  background: #f7fbff;
}
.fish-detail-card .info-item .label {
  color: #86909c;
  font-size: 13px;
  flex-shrink: 0;
}
.fish-detail-card .info-item .value {
  color: #1d2129;
  font-weight: 600;
  text-align: right;
  word-break: break-all;
  font-size: 13px;
}
.fish-detail-nav {
  position: absolute;
  top: 50%;
  z-index: 5;
  transform: translateY(-50%);
  width: 38px;
  height: 64px;
  border: none;
  border-radius: 8px;
  background: rgb(0, 0, 0, 35%);
  color: #fff;
  font-size: 30px;
  line-height: 1;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
}
.fish-detail-nav:hover {
  background: rgb(0, 0, 0, 55%);
}
.fish-detail-nav:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}
.fish-detail-nav-left {
  left: 10px;
}
.fish-detail-nav-right {
  right: 10px;
}

@media (width <= 1000px) {
  .fish-detail-content {
    width: 100%;
    grid-template-columns: minmax(0, 1fr);
  }
  .fish-detail-image-box {
    width: 100%;
    height: 300px;
  }
}
</style>
