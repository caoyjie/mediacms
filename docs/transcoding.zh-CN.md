# MediaCMS 媒体转码

MediaCMS 使用 FFmpeg 转码媒体，主要转码配置位于 `files/helpers.py`，全局参数位于 `cms/settings.py`。

## FFmpeg 预设

```python
FFMPEG_DEFAULT_PRESET = "medium"
```

可选值包括 `ultrafast`、`superfast`、`veryfast`、`faster`、`fast`、`medium`、`slow`、`slower` 和 `veryslow`。速度更快的预设通常会产生更大的文件；速度更慢的预设压缩效率更高，但耗时更长。

## 主要配置项

- `FFMPEG_COMMAND`：FFmpeg 可执行文件路径。
- `FFPROBE_COMMAND`：FFprobe 可执行文件路径。
- `DO_NOT_TRANSCODE_VIDEO`：设为 `True` 时只展示原始视频。
- `CHUNKIZE_VIDEO_DURATION`：超过该秒数的视频会被切片并独立编码。
- `VIDEO_CHUNKS_DURATION`：每个切片的时长，必须小于 `CHUNKIZE_VIDEO_DURATION`。
- `MINIMUM_RESOLUTIONS_TO_ENCODE`：始终需要生成的分辨率，即使这会造成放大。

## 高级配置

如需调整不同编码器和分辨率的码率、音频编码器、音频码率、CRF、关键帧，以及 H.264/H.265/VP9 参数，请修改 `files/helpers.py`。

> 英文原文：[transcoding.md](transcoding.md)
