// Only remove known file suffixes; dots may be part of the actual title.
export function mediaDisplayTitle(title) {
  if (typeof title !== 'string') return title;

  const displayTitle = title.replace(
    /\.(?:mp4|webm|mkv|mov|avi|m4v|mpeg|mpg|mpe|ogv|ogg|wmv|flv|3gp|3g2|mts|m2ts|ts|vob|mp3|m4a|aac|wav|flac|oga|opus|wma|aiff|aif|jpg|jpeg|png|gif|webp|avif|bmp|tif|tiff|svg|heic|heif|pdf)\s*$/i,
    ''
  );

  return displayTitle.trim() ? displayTitle : title;
}
