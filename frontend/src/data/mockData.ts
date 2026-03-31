import type { Category, User } from '../types'

const img = (n: number) => new URL(`../assets/images/samples/300x300/${n}.jpg`, import.meta.url).href
const face = (n: number) => new URL(`../assets/images/faces/face${n}.jpg`, import.meta.url).href

export const mockUser: User = {
  name: 'Allen Moreno',
  role: 'Administrator',
  avatarUrl: face(8),
}

export const mockCategories: Category[] = [
  {
    id: 'cat-1',
    name: 'Nature Scenes',
    description: 'Mountains and Trees',
    iconUrl: img(1),
    voices: [
      { id: 'v1', name: 'Windy Forest', language: 'en', type: 'male', iconUrl: face(1), audioUrl: '/audio/sample.mp3' },
      { id: 'v2', name: 'Bosque Ventoso', language: 'es', type: 'female', iconUrl: face(2), audioUrl: '/audio/sample-es.mp3' },
      { id: 'v3', name: 'Forêt Venteuse', language: 'fr', type: 'ambient', iconUrl: face(3) },
      { id: 'v4', name: 'Ветровита Гора', language: 'bg', type: 'male', iconUrl: face(4) },
    ],
    images: [
      {
        id: 'img-1-1',
        name: 'Mountain Peak',
        files: [img(1), img(2)],
        thumbnailUrl: img(1),
        voices: [
          { id: 'iv1', name: 'Mountain Peak', language: 'en', type: 'male', iconUrl: face(1) },
          { id: 'iv2', name: 'Pico de Montaña', language: 'es', type: 'female', iconUrl: face(2) },
          { id: 'iv3', name: 'Sommet de Montagne', language: 'fr', type: 'male', iconUrl: face(3) },
          { id: 'iv4', name: 'Планински Връх', language: 'bg', type: 'female', iconUrl: face(4) },
        ],
      },
      {
        id: 'img-1-2',
        name: 'River Valley',
        files: [img(2)],
        thumbnailUrl: img(2),
        voices: [
          { id: 'iv5', name: 'River Valley', language: 'en', type: 'male', iconUrl: face(1) },
          { id: 'iv6', name: 'Valle del Río', language: 'es', type: 'female', iconUrl: face(2) },
          { id: 'iv7', name: 'Vallée de la Rivière', language: 'fr', type: 'male', iconUrl: face(3) },
          { id: 'iv8', name: 'Речна Долина', language: 'bg', type: 'female', iconUrl: face(4) },
        ],
      },
      {
        id: 'img-1-3',
        name: 'Dense Forest',
        files: [img(3)],
        thumbnailUrl: img(3),
        voices: [
          { id: 'iv9', name: 'Dense Forest', language: 'en', type: 'male', iconUrl: face(5) },
          { id: 'iv10', name: 'Bosque Denso', language: 'es', type: 'female', iconUrl: face(6) },
          { id: 'iv11', name: 'Forêt Dense', language: 'fr', type: 'male', iconUrl: face(7) },
          { id: 'iv12', name: 'Гъста Гора', language: 'bg', type: 'female', iconUrl: face(8) },
        ],
      },
      {
        id: 'img-1-4',
        name: 'Flower Meadow',
        files: [img(4), img(5)],
        thumbnailUrl: img(4),
        voices: [
          { id: 'iv13', name: 'Flower Meadow', language: 'en', type: 'female', iconUrl: face(1) },
          { id: 'iv14', name: 'Prado de Flores', language: 'es', type: 'female', iconUrl: face(2) },
          { id: 'iv15', name: 'Prairie Fleurie', language: 'fr', type: 'female', iconUrl: face(3) },
          { id: 'iv16', name: 'Цветна Ливада', language: 'bg', type: 'female', iconUrl: face(4) },
        ],
      },
      {
        id: 'img-1-5',
        name: 'Coastal Cliff',
        files: [img(5)],
        thumbnailUrl: img(5),
        voices: [
          { id: 'iv17', name: 'Coastal Cliff', language: 'en', type: 'male', iconUrl: face(5) },
          { id: 'iv18', name: 'Acantilado Costero', language: 'es', type: 'male', iconUrl: face(6) },
          { id: 'iv19', name: 'Falaise Côtière', language: 'fr', type: 'male', iconUrl: face(7) },
          { id: 'iv20', name: 'Крайбрежна Скала', language: 'bg', type: 'male', iconUrl: face(8) },
        ],
      },
      {
        id: 'img-1-6',
        name: 'Misty Hills',
        files: [img(6)],
        thumbnailUrl: img(6),
        voices: [
          { id: 'iv21', name: 'Misty Hills', language: 'en', type: 'ambient', iconUrl: face(9) },
          { id: 'iv22', name: 'Colinas Brumosas', language: 'es', type: 'ambient', iconUrl: face(10) },
          { id: 'iv23', name: 'Collines Brumeuses', language: 'fr', type: 'ambient', iconUrl: face(11) },
          { id: 'iv24', name: 'Мъгливи Хълмове', language: 'bg', type: 'ambient', iconUrl: face(12) },
        ],
      },
    ],
  },
  {
    id: 'cat-2',
    name: 'Urban Landmarks',
    description: 'Cities and Architecture',
    iconUrl: img(7),
    voices: [
      { id: 'v5', name: 'City Guide', language: 'en', type: 'male', iconUrl: face(5) },
      { id: 'v6', name: 'Guía Urbana', language: 'es', type: 'female', iconUrl: face(6) },
      { id: 'v7', name: 'Guide Urbain', language: 'fr', type: 'male', iconUrl: face(7) },
      { id: 'v8', name: 'Градски Пътеводител', language: 'bg', type: 'female', iconUrl: face(8) },
    ],
    images: [
      {
        id: 'img-2-1',
        name: 'Steel Bridge',
        files: [img(7)],
        thumbnailUrl: img(7),
        voices: [
          { id: 'iv25', name: 'Steel Bridge', language: 'en', type: 'male', iconUrl: face(1) },
          { id: 'iv26', name: 'Puente de Acero', language: 'es', type: 'female', iconUrl: face(2) },
          { id: 'iv27', name: 'Pont d\'Acier', language: 'fr', type: 'male', iconUrl: face(3) },
          { id: 'iv28', name: 'Стоманен Мост', language: 'bg', type: 'female', iconUrl: face(4) },
        ],
      },
      {
        id: 'img-2-2',
        name: 'Clock Tower',
        files: [img(8)],
        thumbnailUrl: img(8),
        voices: [
          { id: 'iv29', name: 'Clock Tower', language: 'en', type: 'male', iconUrl: face(5) },
          { id: 'iv30', name: 'Torre del Reloj', language: 'es', type: 'female', iconUrl: face(6) },
          { id: 'iv31', name: 'Tour de l\'Horloge', language: 'fr', type: 'male', iconUrl: face(7) },
          { id: 'iv32', name: 'Часовникова Кула', language: 'bg', type: 'female', iconUrl: face(8) },
        ],
      },
      {
        id: 'img-2-3',
        name: 'Downtown Square',
        files: [img(9), img(10)],
        thumbnailUrl: img(9),
        voices: [
          { id: 'iv33', name: 'Downtown Square', language: 'en', type: 'female', iconUrl: face(9) },
          { id: 'iv34', name: 'Plaza Central', language: 'es', type: 'female', iconUrl: face(10) },
          { id: 'iv35', name: 'Place Centrale', language: 'fr', type: 'female', iconUrl: face(11) },
          { id: 'iv36', name: 'Централен Площад', language: 'bg', type: 'female', iconUrl: face(12) },
        ],
      },
      {
        id: 'img-2-4',
        name: 'Modern Skyline',
        files: [img(10)],
        thumbnailUrl: img(10),
        voices: [
          { id: 'iv37', name: 'Modern Skyline', language: 'en', type: 'male', iconUrl: face(1) },
          { id: 'iv38', name: 'Horizonte Moderno', language: 'es', type: 'male', iconUrl: face(2) },
          { id: 'iv39', name: 'Horizon Moderne', language: 'fr', type: 'male', iconUrl: face(3) },
          { id: 'iv40', name: 'Модерен Хоризонт', language: 'bg', type: 'male', iconUrl: face(4) },
        ],
      },
    ],
  },
  {
    id: 'cat-3',
    name: 'Portrait Photos',
    description: 'People and Faces',
    iconUrl: face(14),
    voices: [
      { id: 'v9', name: 'Portrait Narrator', language: 'en', type: 'female', iconUrl: face(9) },
      { id: 'v10', name: 'Narrador de Retratos', language: 'es', type: 'male', iconUrl: face(10) },
      { id: 'v11', name: 'Narrateur de Portraits', language: 'fr', type: 'female', iconUrl: face(11) },
      { id: 'v12', name: 'Разказвач на Портрети', language: 'bg', type: 'male', iconUrl: face(12) },
    ],
    images: [
      {
        id: 'img-3-1',
        name: 'Serene Nature',
        files: [img(11)],
        thumbnailUrl: img(11),
        voices: [
          { id: 'iv41', name: 'Serene Nature', language: 'en', type: 'female', iconUrl: face(13) },
          { id: 'iv42', name: 'Naturaleza Serena', language: 'es', type: 'female', iconUrl: face(14) },
          { id: 'iv43', name: 'Nature Sereine', language: 'fr', type: 'female', iconUrl: face(15) },
          { id: 'iv44', name: 'Спокойна Природа', language: 'bg', type: 'female', iconUrl: face(16) },
        ],
      },
      {
        id: 'img-3-2',
        name: 'Studio Portrait',
        files: [img(12)],
        thumbnailUrl: img(12),
        voices: [
          { id: 'iv45', name: 'Studio Portrait', language: 'en', type: 'male', iconUrl: face(17) },
          { id: 'iv46', name: 'Retrato de Estudio', language: 'es', type: 'male', iconUrl: face(18) },
          { id: 'iv47', name: 'Portrait en Studio', language: 'fr', type: 'male', iconUrl: face(19) },
          { id: 'iv48', name: 'Студиен Портрет', language: 'bg', type: 'male', iconUrl: face(20) },
        ],
      },
      {
        id: 'img-3-3',
        name: 'Candid Smile',
        files: [img(13), img(14)],
        thumbnailUrl: img(13),
        voices: [
          { id: 'iv49', name: 'Candid Smile', language: 'en', type: 'female', iconUrl: face(21) },
          { id: 'iv50', name: 'Sonrisa Espontánea', language: 'es', type: 'female', iconUrl: face(22) },
          { id: 'iv51', name: 'Sourire Spontané', language: 'fr', type: 'female', iconUrl: face(23) },
          { id: 'iv52', name: 'Искрена Усмивка', language: 'bg', type: 'female', iconUrl: face(24) },
        ],
      },
      {
        id: 'img-3-4',
        name: 'Golden Hour',
        files: [img(14)],
        thumbnailUrl: img(14),
        voices: [
          { id: 'iv53', name: 'Golden Hour', language: 'en', type: 'ambient', iconUrl: face(25) },
          { id: 'iv54', name: 'Hora Dorada', language: 'es', type: 'ambient', iconUrl: face(1) },
          { id: 'iv55', name: 'Heure Dorée', language: 'fr', type: 'ambient', iconUrl: face(2) },
          { id: 'iv56', name: 'Златен Час', language: 'bg', type: 'ambient', iconUrl: face(3) },
        ],
      },
      {
        id: 'img-3-5',
        name: 'Winter Portrait',
        files: [img(15)],
        thumbnailUrl: img(15),
        voices: [
          { id: 'iv57', name: 'Winter Portrait', language: 'en', type: 'female', iconUrl: face(4) },
          { id: 'iv58', name: 'Retrato de Invierno', language: 'es', type: 'female', iconUrl: face(5) },
          { id: 'iv59', name: 'Portrait d\'Hiver', language: 'fr', type: 'female', iconUrl: face(6) },
          { id: 'iv60', name: 'Зимен Портрет', language: 'bg', type: 'female', iconUrl: face(7) },
        ],
      },
    ],
  },
  {
    id: 'cat-4',
    name: 'Abstract Textures',
    description: 'Patterns and Colors',
    iconUrl: img(15),
    voices: [
      { id: 'v13', name: 'Texture Voice', language: 'en', type: 'ambient', iconUrl: face(13) },
      { id: 'v14', name: 'Voz de Textura', language: 'es', type: 'ambient', iconUrl: face(14) },
      { id: 'v15', name: 'Voix de Texture', language: 'fr', type: 'ambient', iconUrl: face(15) },
      { id: 'v16', name: 'Глас на Текстура', language: 'bg', type: 'ambient', iconUrl: face(16) },
    ],
    images: [
      {
        id: 'img-4-1',
        name: 'Marble Surface',
        files: [img(1), img(3)],
        thumbnailUrl: img(1),
        voices: [
          { id: 'iv61', name: 'Marble Surface', language: 'en', type: 'ambient', iconUrl: face(8) },
          { id: 'iv62', name: 'Superficie de Mármol', language: 'es', type: 'ambient', iconUrl: face(9) },
          { id: 'iv63', name: 'Surface de Marbre', language: 'fr', type: 'ambient', iconUrl: face(10) },
          { id: 'iv64', name: 'Мраморна Повърхност', language: 'bg', type: 'ambient', iconUrl: face(11) },
        ],
      },
      {
        id: 'img-4-2',
        name: 'Woven Fabric',
        files: [img(5)],
        thumbnailUrl: img(5),
        voices: [
          { id: 'iv65', name: 'Woven Fabric', language: 'en', type: 'ambient', iconUrl: face(12) },
          { id: 'iv66', name: 'Tela Tejida', language: 'es', type: 'ambient', iconUrl: face(13) },
          { id: 'iv67', name: 'Tissu Tissé', language: 'fr', type: 'ambient', iconUrl: face(14) },
          { id: 'iv68', name: 'Тъкана Материя', language: 'bg', type: 'ambient', iconUrl: face(15) },
        ],
      },
      {
        id: 'img-4-3',
        name: 'Rust Pattern',
        files: [img(9)],
        thumbnailUrl: img(9),
        voices: [
          { id: 'iv69', name: 'Rust Pattern', language: 'en', type: 'male', iconUrl: face(16) },
          { id: 'iv70', name: 'Patrón de Óxido', language: 'es', type: 'male', iconUrl: face(17) },
          { id: 'iv71', name: 'Motif de Rouille', language: 'fr', type: 'male', iconUrl: face(18) },
          { id: 'iv72', name: 'Модел на Ръжда', language: 'bg', type: 'male', iconUrl: face(19) },
        ],
      },
      {
        id: 'img-4-4',
        name: 'Water Ripple',
        files: [img(11), img(13)],
        thumbnailUrl: img(11),
        voices: [
          { id: 'iv73', name: 'Water Ripple', language: 'en', type: 'ambient', iconUrl: face(20) },
          { id: 'iv74', name: 'Ondulación del Agua', language: 'es', type: 'ambient', iconUrl: face(21) },
          { id: 'iv75', name: 'Ondulation de l\'Eau', language: 'fr', type: 'ambient', iconUrl: face(22) },
          { id: 'iv76', name: 'Водна Вълничка', language: 'bg', type: 'ambient', iconUrl: face(23) },
        ],
      },
    ],
  },
  {
    id: 'cat-5',
    name: 'Ocean Life',
    description: 'Sea Creatures and Coral',
    iconUrl: img(6),
    voices: [
      { id: 'v17', name: 'Ocean Narrator', language: 'en', type: 'ambient', iconUrl: face(5) },
      { id: 'v18', name: 'Narrador del Océano', language: 'es', type: 'ambient', iconUrl: face(6) },
      { id: 'v19', name: 'Narrateur de l\'Océan', language: 'fr', type: 'ambient', iconUrl: face(7) },
      { id: 'v20', name: 'Океански Разказвач', language: 'bg', type: 'ambient', iconUrl: face(8) },
    ],
    images: [
      {
        id: 'img-5-1',
        name: 'Coral Reef',
        files: [img(8)],
        thumbnailUrl: img(8),
        voices: [
          { id: 'iv77', name: 'Coral Reef', language: 'en', type: 'ambient', iconUrl: face(9) },
          { id: 'iv78', name: 'Arrecife de Coral', language: 'es', type: 'ambient', iconUrl: face(10) },
          { id: 'iv79', name: 'Récif Corallien', language: 'fr', type: 'ambient', iconUrl: face(11) },
          { id: 'iv80', name: 'Коралов Риф', language: 'bg', type: 'ambient', iconUrl: face(12) },
        ],
      },
    ],
  },
]
