export const themePresets = [
  { id: 'aurora', name: 'Aurora', description: 'Lilac skies, a blush of pink.', dark: false, colors: ['#dcd5f4', '#f9e0eb', '#e2eafa'], accent: '#7353a3' },
  { id: 'blossom', name: 'Blossom', description: 'Peach light and a little rose.', dark: false, colors: ['#f8ddc9', '#f7dce6', '#fff0da'], accent: '#a34f59' },
  { id: 'lagoon', name: 'Lagoon', description: 'Clear skies meet quiet waters.', dark: false, colors: ['#cfe6fa', '#d4eee9', '#e1e7fb'], accent: '#236f86' },
  { id: 'meadow', name: 'Meadow', description: 'Soft sage in the morning sun.', dark: false, colors: ['#dce8d0', '#f5edcc', '#dfede3'], accent: '#526f36' },
  { id: 'light', name: 'Light', description: 'Simple, bright, familiar.', dark: false, classic: true, colors: ['#fafafa', '#ffffff', '#eeeeee'], accent: '#009933' },
  { id: 'dark', name: 'Dark', description: 'A classic for after hours.', dark: true, classic: true, colors: ['#181818', '#272727', '#353535'], accent: '#009933' },
];

export function isThemeMode(mode) {
  return themePresets.some((theme) => theme.id === mode);
}

export function isDarkTheme(mode) {
  return themePresets.some((theme) => theme.id === mode && theme.dark);
}
