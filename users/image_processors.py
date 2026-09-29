class CenterCropBanner:
    """Crop to the banner's 9:2 aspect ratio without resampling pixels."""

    def process(self, image):
        width, height = image.size
        if width * 2 == height * 9:
            return image

        if width * 2 > height * 9:
            crop_width, crop_height = max(1, height * 9 // 2), height
        else:
            crop_width, crop_height = width, max(1, width * 2 // 9)

        left = (width - crop_width) // 2
        top = (height - crop_height) // 2
        return image.crop((left, top, left + crop_width, top + crop_height))
