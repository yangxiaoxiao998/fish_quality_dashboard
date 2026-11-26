<template>
  <div class="image-gallery-page">
    <a-breadcrumb class="page-breadcrumb">
      <a-breadcrumb-item>首页</a-breadcrumb-item>
      <a-breadcrumb-item>数据管理</a-breadcrumb-item>
      <a-breadcrumb-item>查看原图</a-breadcrumb-item>
    </a-breadcrumb>

    <div class="page-header">
      <h2>查看原图</h2>
    </div>

    <div class="filter-section">
      <a-row :gutter="16">
        <a-col :span="6">
          <div class="filter-item">
            <label>鱼ID</label>
            <a-input v-model="filters.fishId" placeholder="请输入鱼ID" allow-clear @press-enter="handleSearch" />
          </div>
        </a-col>
        <a-col :span="6">
          <div class="filter-item">
            <label>视频ID</label>
            <a-input v-model="filters.videoId" placeholder="请输入视频ID" allow-clear @press-enter="handleSearch" />
          </div>
        </a-col>
        <a-col :span="6">
          <div class="filter-actions">
            <a-button type="primary" @click="handleSearch">
              <template #icon><icon-search /></template>
              搜索
            </a-button>
            <a-button @click="handleReset">重置</a-button>
          </div>
        </a-col>
      </a-row>
    </div>

    <div class="action-bar">
      <a-button type="primary" @click="handleBatchDownload">
        <template #icon><icon-download /></template>
        批量下载
      </a-button>
    </div>

    <div class="image-grid">
      <div v-for="img in tableData" :key="img.id" class="image-card">
        <div class="image-wrapper">
          <img :src="normalizeReportUrl(img.url)" :alt="img.name" @error="handleImageError" />
        </div>
        <div class="image-info">
          <a-tooltip :content="img.id">
            <div class="image-name">{{ img.name }}</div>
          </a-tooltip>
          <div class="image-time">{{ img.time }}</div>
        </div>
        <div class="image-actions">
          <a-button type="text" size="small" @click="handleDownload(img)">
            <template #icon><icon-download /></template>
          </a-button>
        </div>
      </div>
      <div v-if="tableData.length === 0 && !loading" class="no-data">
        暂无数据
      </div>
    </div>

    <div class="pagination-wrap">
      <a-pagination
        v-model:current="currentPage"
        v-model:page-size="pageSize"
        :total="total"
        :page-size-options="[20, 50, 100]"
        show-total
        show-page-size
        @change="handlePageChange"
        @page-size-change="handlePageSizeChange"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, onBeforeUnmount } from 'vue'
import { http } from '@/api/http'
import fishPlaceholder from '@/assets/images/fish-placeholder.svg'

interface ImageRecord {
  id: string
  url: string
  name: string
  time: string
  fish_id: string
  fish_display_id?: string
  fish_short_id?: string
  video_id: string
  video_display_id?: string
}
import { normalizeReportUrl } from '@/utils/reportPath'

const filters = reactive({
  fishId: '',
  videoId: '',
})

const currentPage = ref(1)
const pageSize = ref(20)
const total = ref(0)
const tableData = ref<ImageRecord[]>([])
const loading = ref(false)

async function fetchData() {
  loading.value = true
  try {
    const params: Record<string, any> = {
      page: currentPage.value,
      page_size: pageSize.value,
    }
    
    if (filters.fishId) params.fish_id = filters.fishId
    if (filters.videoId) params.video_id = filters.videoId
    
    const resp = await http.get('/api/images/original', { params })
    const data = resp.data
    
    tableData.value = data.items || []
    total.value = data.total || 0
  } catch (err) {
    console.error('Failed to fetch images:', err)
  } finally {
    loading.value = false
  }
}

function handleSearch() {
  currentPage.value = 1
  fetchData()
}

function handleReset() {
  filters.fishId = ''
  filters.videoId = ''
  currentPage.value = 1
  fetchData()
}

function handlePageChange(page: number) {
  currentPage.value = page
  fetchData()
}

function handlePageSizeChange(size: number) {
  pageSize.value = size
  currentPage.value = 1
  fetchData()
}

function handleImageError(e: Event) {
  const img = e.target as HTMLImageElement
  img.onerror = null
  img.src = fishPlaceholder
}

function handleDownload(img: ImageRecord) {
  const a = document.createElement('a')
  a.href = normalizeReportUrl(img.url)
  a.download = img.name
  a.target = '_blank'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

function handleBatchDownload() {
  const apiDomain = import.meta.env.VITE_API_DOMAIN || 'http://localhost:8000'
  fetch(`${apiDomain}/api/images/original/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  })
    .then((res) => res.blob())
    .then((blob) => {
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'original_images_export.zip'
      a.click()
      URL.revokeObjectURL(url)
    })
    .catch((err) => {
      console.error('Failed to export images:', err)
    })
}

onMounted(() => {
  fetchData()
  window.addEventListener('focus', fetchData)
})

onBeforeUnmount(() => {
  window.removeEventListener('focus', fetchData)
})
</script>

<style lang="scss" scoped>
.image-gallery-page {
  padding: 20px;
  overflow: auto;
  box-sizing: border-box;
  background: #f7f8fa;
  .page-breadcrumb {
    margin-bottom: 16px;
  }
  .page-header {
    margin-bottom: 20px;
    h2 {
      margin: 0;
      font-size: 20px;
      font-weight: 600;
      color: #1d2129;
    }
  }
  .filter-section {
    background: #fff;
    padding: 20px;
    border-radius: 8px;
    margin-bottom: 16px;
    box-shadow: 0 2px 8px rgb(0, 0, 0, 6%);
    .filter-item {
      label {
        display: block;
        margin-bottom: 8px;
        font-size: 13px;
        color: #4e5969;
      }
    }
    .filter-actions {
      display: flex;
      gap: 12px;
      align-items: flex-end;
    }
  }
  .action-bar {
    margin-bottom: 16px;
  }
  .image-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
    gap: 16px;
    margin-bottom: 20px;
    .image-card {
      background: #fff;
      border-radius: 8px;
      overflow: hidden;
      box-shadow: 0 2px 8px rgb(0, 0, 0, 6%);
      .image-wrapper {
        width: 100%;
        height: 150px;
        display: flex;
        align-items: center;
        justify-content: center;
        background: #f7f8fa;
        img {
          max-width: 100%;
          max-height: 100%;
          object-fit: contain;
        }
      }
      .image-info {
        padding: 8px;
        .image-name {
          font-size: 12px;
          color: #4e5969;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .image-time {
          font-size: 11px;
          color: #86909c;
          margin-top: 4px;
        }
      }
      .image-actions {
        padding: 0 8px 8px;
        display: flex;
        justify-content: flex-end;
      }
    }
    .no-data {
      grid-column: 1 / -1;
      text-align: center;
      padding: 40px;
      color: #86909c;
    }
  }
  .pagination-wrap {
    display: flex;
    justify-content: flex-end;
  }
}
</style>
