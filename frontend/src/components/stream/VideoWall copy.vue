<!-- 在 frontend/src/components/stream/VideoWall.vue -->
<template>
  <div class="video-wall">
    <div class="toolbar">
      <div class="toolbar-left">
        <div class="toolbar-title">视频流输入</div>
        <div class="toolbar-sub">支持：本地视频预览</div>
      </div>

      <div class="toolbar-right">
        <input
          ref="fileInput"
          class="file-input"
          type="file"
          accept="video/*"
          @change="onFileChange"
        />
        <button class="btn" @click="triggerPick">选择并上传视频</button>
        <button class="btn" :disabled="!videoUrl" @click="togglePlay">
          {{ playing ? '暂停' : '播放' }}
        </button>
        <button class="btn" :disabled="!videoUrl" @click="reset">重置</button>
      </div>
    </div>

    <div class="grid">
      <div class="cell">
        <div class="stage">
          <!-- 修正：确保 video 标签结构清晰 -->
          <video
            ref="v1"
            class="video"
            :src="videoUrl"
            playsinline
            muted
            loop
            @play="playing = true"
            @pause="playing = false"
          ></video>
          <!-- /video 标签结束 -->
          <div v-if="!videoUrl" class="placeholder">
            <div class="placeholder-title">未选择视频</div>
            <div class="placeholder-sub">点击"选择视频"后预览</div>
          </div>
        </div>
      </div>
    </div>

    <div class="hint">
      <!-- 用来显示上传状态信息 -->
      <p v-if="uploadStatus">{{ uploadStatus }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

const fileInput = ref<HTMLInputElement | null>(null)
const v1 = ref<HTMLVideoElement | null>(null)

// 现在 videoUrl 是动态获取的，初始为空
const videoUrl = ref<string>('')
const playing = ref(false)
const uploadStatus = ref<string>('')

function triggerPick() {
  if (fileInput.value) {
    fileInput.value.click()
  }
}

// 定义一个上传函数
async function uploadFile(file: File) {
  const formData = new FormData();
  formData.append('file', file);

  try {
    uploadStatus.value = '上传中...';
    // 调用后端的上传接口（同源 /api）
    const response = await fetch('/api/stream/upload', {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) {
      throw new Error(`上传失败: ${response.statusText}`);
    }

    // 解析后端返回的 JSON 数据
    const result = await response.json();
    console.log('上传成功:', result);

    // 从后端响应中获取播放URL
    if (result.playback_url) {
      // --- 关键改动：不再需要路径修正 ---
      // 后端 main.py 已修改，返回的 URL 应该是正确的
      const correctedPlaybackUrl = result.playback_url; 
      // --- 关键改动结束 ---

      // 清除之前的URL对象 (如果有的话)
      // 这行代码不再需要，因为我们不再使用 createObjectURL
      // if (videoUrl.value && videoUrl.value.startsWith('blob:')) {
      //   URL.revokeObjectURL(videoUrl.value);
      // }
      
      // 设置新的播放URL
      videoUrl.value = correctedPlaybackUrl; // 直接使用后端返回的 URL
      uploadStatus.value = `上传成功，Video ID: ${result.video_id}`;
      
      // 自动播放新视频
      if (v1.value) {
        v1.value.currentTime = 0; // 重置到开头
        await v1.value.play();
        playing.value = true;
      }
    } else {
      throw new Error('服务器未返回播放链接');
    }
  } catch (error) {
    console.error('Upload error:', error);
    uploadStatus.value = `上传失败: ${(error as Error).message}`;
  }
}

async function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  const f = input.files?.[0]
  if (!f) {
    return
  }

  // 调用上传函数
  await uploadFile(f);
}

async function togglePlay() {
  if (!videoUrl.value || !v1.value) {
    return
  }

  try {
    if (playing.value) {
      v1.value.pause()
      playing.value = false
    } else {
      await v1.value.play()
      playing.value = true
    }
  } catch (error) {
    console.error('播放失败:', error)
  }
}

function reset() {
  if (v1.value) {
    v1.value.currentTime = 0
    v1.value.pause()
  }
  playing.value = false
  // 重置视频源
  // 同样，这行也不再需要
  // if (videoUrl.value && videoUrl.value.startsWith('blob:')) {
  //   URL.revokeObjectURL(videoUrl.value);
  // }
  videoUrl.value = ''
  uploadStatus.value = '';
}
</script>

<style scoped>
.video-wall {
  height: 100vh;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 0;
  margin: 0;
}
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 14px;
  padding: 0 12px;
}
.toolbar-left {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.toolbar-title {
  font-weight: 800;
  font-size: 16px;
}
.toolbar-sub {
  font-size: 12px;
  opacity: 0.7;
}
.toolbar-right {
  display: flex;
  align-items: center;
  gap: 10px;
}
.file-input {
  display: none;
}
.btn {
  padding: 8px 12px;
  border-radius: 10px;
  border-width: 1px;
  border-style: solid;
  border-color: rgb(255, 255, 255, 18%);
  background: rgb(255, 255, 255, 6%);
  color: inherit;
  cursor: pointer;
}
.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.grid {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: 1fr;
  gap: 12px;
}
.cell {
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.cell-title {
  font-weight: 700;
  opacity: 0.85;
}
.stage {
  position: relative;
  flex: 1;
  min-height: 0;
  border-radius: 14px;
  overflow: hidden;
  border-width: 1px;
  border-style: solid;
  border-color: rgb(255, 255, 255, 10%);
  background: rgb(0, 0, 0, 20%);
}
.video {
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
  background: rgb(0, 0, 0, 15%);
}
.placeholder {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: center;
  justify-content: center;
  pointer-events: none;
}
.placeholder-title {
  font-weight: 800;
  opacity: 0.85;
}
.placeholder-sub {
  font-size: 12px;
  opacity: 0.65;
  text-align: center;
  padding-left: 12px;
  padding-right: 12px;
}
.hint {
  font-size: 12px;
  opacity: 0.65;
}
</style>