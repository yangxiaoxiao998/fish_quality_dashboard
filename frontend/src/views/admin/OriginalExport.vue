<template>
  <div class="export-page">
    <!-- 筛选区 -->
    <div class="filter-section">
      <div class="filter-item">
        <label>日期范围</label>
        <div class="date-range">
          <input type="date" v-model="startDate" class="date-input" />
          <span>至</span>
          <input type="date" v-model="endDate" class="date-input" />
        </div>
      </div>
      <button class="btn-search" @click="handleSearch">
        <i class="fa-solid fa-magnifying-glass"></i> 搜索
      </button>
      <button class="btn-export" @click="handleExport" :disabled="exporting">
        <i class="fa-solid fa-file-zipper"></i>
        {{ exporting ? `导出中 ${progress}%` : '导出原图压缩包' }}
      </button>
    </div>

    <!-- 进度条 -->
    <div v-if="exporting" class="progress-section">
      <div class="progress-bar">
        <div class="progress-fill" :style="{ width: progress + '%' }"></div>
      </div>
      <span class="progress-text">{{ exportedCount }} / {{ totalCount }} 张</span>
    </div>

    <!-- 图片预览网格 -->
    <div class="image-grid">
      <div v-for="img in paginatedImages" :key="img.id" class="image-card">
        <div class="image-wrapper">
          <img :src="img.url" :alt="img.name" />
          <div class="image-overlay">
            <button class="btn-preview" @click="previewImage(img)">
              <i class="fa-solid fa-eye"></i>
            </button>
            <button class="btn-download" @click="downloadImage(img)">
              <i class="fa-solid fa-download"></i>
            </button>
          </div>
        </div>
        <div class="image-info">
          <span class="image-name">{{ img.name }}</span>
          <span class="image-time">{{ img.time }}</span>
        </div>
      </div>
    </div>

    <!-- 分页 -->
    <div class="pagination">
      <div class="pagination-info">共 {{ totalCount }} 张</div>
      <div class="pagination-controls">
        <button :disabled="currentPage === 1" @click="currentPage--">上一页</button>
        <span>{{ currentPage }} / {{ totalPages }}</span>
        <button :disabled="currentPage === totalPages" @click="currentPage++">下一页</button>
      </div>
    </div>

    <!-- 预览弹窗 -->
    <div v-if="previewVisible" class="preview-modal" @click="previewVisible = false">
      <div class="preview-content" @click.stop>
        <img :src="currentPreview?.url" :alt="currentPreview?.name" />
        <div class="preview-info">
          <span>{{ currentPreview?.name }}</span>
          <span>{{ currentPreview?.time }}</span>
        </div>
        <button class="close-btn" @click="previewVisible = false">
          <i class="fa-solid fa-times"></i>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import dayjs from 'dayjs'
import fishPlaceholder from '@/assets/images/fish-placeholder.svg'

const startDate = ref('')
const endDate = ref('')
const currentPage = ref(1)
const pageSize = ref(20)
const exporting = ref(false)
const progress = ref(0)
const exportedCount = ref(0)
const totalCount = ref(50)
const previewVisible = ref(false)
const currentPreview = ref<ImageItem | null>(null)

interface ImageItem {
  id: string
  url: string
  name: string
  time: string
}

const mockImages: ImageItem[] = Array.from({ length: 50 }, (_, i) => ({
  id: `img_${i}`,
  url: fishPlaceholder,
  name: `original_${String(i + 1).padStart(4, '0')}.jpg`,
  time: `2026-03-0${(i % 9) + 1} ${String((i % 12) + 8).padStart(2, '0')}:${String((i * 7) % 60).padStart(2, '0')}`
}))

const totalPages = computed(() => Math.ceil(totalCount.value / pageSize.value))

const paginatedImages = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return mockImages.slice(start, start + pageSize.value)
})

function handleSearch() {
  currentPage.value = 1
}

function handleExport() {
  if (exporting.value) return
  
  exporting.value = true
  progress.value = 0
  exportedCount.value = 0
  
  const interval = setInterval(() => {
    progress.value += 10
    exportedCount.value = Math.floor(totalCount.value * progress.value / 100)
    
    if (progress.value >= 100) {
      clearInterval(interval)
      exporting.value = false
      const date = dayjs().format('YYYYMMDD')
      console.log(`导出完成: fish_original_${date}.zip`)
    }
  }, 200)
}

function previewImage(img: ImageItem) {
  currentPreview.value = img
  previewVisible.value = true
}

function downloadImage(img: ImageItem) {
  console.log('下载图片', img)
}
</script>

<style lang="scss" scoped>
.export-page {
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.filter-section {
  display: flex;
  gap: 20px;
  align-items: flex-end;
  background: rgb(26, 26, 46, 80%);
  border: 1px solid rgb(0, 168, 232, 30%);
  border-radius: 8px;
  padding: 20px;
  .filter-item {
    display: flex;
    flex-direction: column;
    gap: 6px;
    label {
      color: rgb(255, 255, 255, 70%);
      font-size: 12px;
    }
    .date-range {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .date-input {
      background: rgb(22, 33, 62, 80%);
      border: 1px solid rgb(0, 168, 232, 30%);
      border-radius: 4px;
      padding: 8px 12px;
      color: #fff;
    }
  }
  .btn-search,
  .btn-export {
    padding: 8px 20px;
    border-radius: 4px;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 8px;
    transition: all 0.3s ease;
  }
  .btn-search {
    background: linear-gradient(135deg, #00a8e8 0%, #00f5d4 100%);
    border: none;
    color: #fff;
  }
  .btn-export {
    background: rgb(0, 168, 232, 20%);
    border: 1px solid rgb(0, 168, 232, 50%);
    color: #00a8e8;
    &:hover:not(:disabled) {
      background: rgb(0, 168, 232, 40%);
    }
    &:disabled {
      opacity: 0.7;
      cursor: not-allowed;
    }
  }
}
.progress-section {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 16px;
  background: rgb(26, 26, 46, 80%);
  border-radius: 8px;
  .progress-bar {
    flex: 1;
    height: 8px;
    background: rgb(255, 255, 255, 10%);
    border-radius: 4px;
    overflow: hidden;
    .progress-fill {
      height: 100%;
      background: linear-gradient(90deg, #00a8e8, #00f5d4);
      transition: width 0.3s ease;
    }
  }
  .progress-text {
    color: rgb(255, 255, 255, 70%);
    font-size: 14px;
    min-width: 120px;
  }
}
.image-grid {
  flex: 1;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 16px;
  overflow-y: auto;
  padding: 4px;
  .image-card {
    background: rgb(26, 26, 46, 60%);
    border: 1px solid rgb(0, 168, 232, 20%);
    border-radius: 8px;
    overflow: hidden;
    transition: all 0.3s ease;
    &:hover {
      border-color: #00a8e8;
      box-shadow: 0 0 20px rgb(0, 168, 232, 30%);
    }
    .image-wrapper {
      position: relative;
      aspect-ratio: 4/3;
      overflow: hidden;
      img {
        width: 100%;
        height: 100%;
        object-fit: cover;
      }
      .image-overlay {
        position: absolute;
        inset: 0;
        background: rgb(0, 0, 0, 60%);
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 12px;
        opacity: 0;
        transition: opacity 0.2s;
        button {
          background: rgb(0, 168, 232, 80%);
          border: none;
          border-radius: 50%;
          width: 36px;
          height: 36px;
          color: #fff;
          cursor: pointer;
          transition: all 0.2s;
          &:hover {
            background: #00a8e8;
            transform: scale(1.1);
          }
        }
      }
    }
    &:hover .image-overlay {
      opacity: 1;
    }
    .image-info {
      padding: 10px;
      display: flex;
      flex-direction: column;
      gap: 4px;
      .image-name {
        color: #fff;
        font-size: 12px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .image-time {
        color: rgb(255, 255, 255, 50%);
        font-size: 11px;
      }
    }
  }
}
.pagination {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  background: rgb(26, 26, 46, 80%);
  border-radius: 8px;
  .pagination-info { color: rgb(255, 255, 255, 70%); }
  .pagination-controls {
    display: flex;
    gap: 12px;
    align-items: center;
    button {
      background: rgb(0, 168, 232, 20%);
      border: 1px solid rgb(0, 168, 232, 30%);
      border-radius: 4px;
      padding: 6px 12px;
      color: #fff;
      cursor: pointer;
      &:disabled { opacity: 0.4; cursor: not-allowed; }
      &:hover:not(:disabled) { background: rgb(0, 168, 232, 40%); }
    }
  }
}
.preview-modal {
  position: fixed;
  inset: 0;
  background: rgb(0, 0, 0, 90%);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 2000;
  .preview-content {
    position: relative;
    max-width: 90%;
    max-height: 90%;
    display: flex;
    flex-direction: column;
    align-items: center;
    img {
      max-width: 100%;
      max-height: 80vh;
      border-radius: 8px;
    }
    .preview-info {
      margin-top: 16px;
      display: flex;
      gap: 20px;
      color: rgb(255, 255, 255, 80%);
    }
    .close-btn {
      position: absolute;
      top: -40px;
      right: 0;
      background: transparent;
      border: none;
      color: #fff;
      font-size: 24px;
      cursor: pointer;
    }
  }
}
</style>
