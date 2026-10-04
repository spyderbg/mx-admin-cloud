import type { Language } from '../types/viewModels'
import { serviceDisplayName } from './configurationNavigation'

interface ConfigurationLabels {
  description: string
  tryAgain: string
  noServices: string
  loadingConfigurations: string
  countUnavailable: string
  configuration: string
  configurations: string
  active: string
  activePlural: string
  serviceConfigurations: string
  expandAllServices: string
  collapseAllServices: string
  noServicesMatch: string
  noConfigurations: string
  noConfigurationsMatch: string
  retryConfigurations: string
  allServices: string
  services: Record<string, string>
}

const translations: Record<Language, ConfigurationLabels> = {
  en: {
    description: 'Select a service to manage its provider configurations.',
    tryAgain: 'Try again',
    noServices: 'No configurable services are available.',
    loadingConfigurations: 'Loading configurations…',
    countUnavailable: 'Configuration count unavailable',
    configuration: 'configuration', configurations: 'configurations',
    active: 'active', activePlural: 'active',
    serviceConfigurations: 'Service configurations',
    expandAllServices: 'Expand all services',
    collapseAllServices: 'Collapse all services',
    noServicesMatch: 'No services match',
    noConfigurations: 'No configurations',
    noConfigurationsMatch: 'No matching configurations',
    retryConfigurations: 'Retry loading configurations',
    allServices: 'All services',
    services: {
      content: 'Content', logging: 'Logging',
      text_to_image: 'Text To Image', text_to_speech: 'Text To Speech',
      text_translation: 'Text Translation', voice_optimization: 'Voice Optimization',
    },
  },
  es: {
    description: 'Selecciona un servicio para gestionar la configuración de sus proveedores.',
    tryAgain: 'Reintentar',
    noServices: 'No hay servicios configurables disponibles.',
    loadingConfigurations: 'Cargando configuraciones…',
    countUnavailable: 'Cantidad de configuraciones no disponible',
    configuration: 'configuración', configurations: 'configuraciones',
    active: 'activa', activePlural: 'activas',
    serviceConfigurations: 'Configuraciones de servicios',
    expandAllServices: 'Expandir todos los servicios',
    collapseAllServices: 'Contraer todos los servicios',
    noServicesMatch: 'Ningún servicio coincide',
    noConfigurations: 'No hay configuraciones',
    noConfigurationsMatch: 'No hay configuraciones coincidentes',
    retryConfigurations: 'Reintentar la carga de configuraciones',
    allServices: 'Todos los servicios',
    services: {
      content: 'Contenido', logging: 'Registro',
      text_to_image: 'Texto a imagen', text_to_speech: 'Texto a voz',
      text_translation: 'Traducción de texto', voice_optimization: 'Optimización de voz',
    },
  },
  fr: {
    description: 'Sélectionnez un service pour gérer la configuration de ses fournisseurs.',
    tryAgain: 'Réessayer',
    noServices: "Aucun service configurable n’est disponible.",
    loadingConfigurations: 'Chargement des configurations…',
    countUnavailable: 'Nombre de configurations indisponible',
    configuration: 'configuration', configurations: 'configurations',
    active: 'active', activePlural: 'actives',
    serviceConfigurations: 'Configurations des services',
    expandAllServices: 'Développer tous les services',
    collapseAllServices: 'Réduire tous les services',
    noServicesMatch: 'Aucun service correspondant',
    noConfigurations: 'Aucune configuration',
    noConfigurationsMatch: 'Aucune configuration correspondante',
    retryConfigurations: 'Réessayer de charger les configurations',
    allServices: 'Tous les services',
    services: {
      content: 'Contenu', logging: 'Journalisation',
      text_to_image: 'Texte en image', text_to_speech: 'Texte en parole',
      text_translation: 'Traduction de texte', voice_optimization: 'Optimisation de la voix',
    },
  },
  bg: {
    description: 'Изберете услуга, за да управлявате конфигурациите на нейните доставчици.',
    tryAgain: 'Опитайте отново',
    noServices: 'Няма налични услуги за конфигуриране.',
    loadingConfigurations: 'Зареждане на конфигурациите…',
    countUnavailable: 'Броят на конфигурациите е недостъпен',
    configuration: 'конфигурация', configurations: 'конфигурации',
    active: 'активна', activePlural: 'активни',
    serviceConfigurations: 'Конфигурации на услугите',
    expandAllServices: 'Разгъни всички услуги',
    collapseAllServices: 'Сгъни всички услуги',
    noServicesMatch: 'Няма съответстващи услуги',
    noConfigurations: 'Няма конфигурации',
    noConfigurationsMatch: 'Няма съответстващи конфигурации',
    retryConfigurations: 'Зареди конфигурациите отново',
    allServices: 'Всички услуги',
    services: {
      content: 'Съдържание', logging: 'Журнали',
      text_to_image: 'Текст в изображение', text_to_speech: 'Текст в реч',
      text_translation: 'Превод на текст', voice_optimization: 'Оптимизация на гласа',
    },
  },
}

export function configurationLabels(lang: Language): ConfigurationLabels {
  return translations[lang] ?? translations.en
}

export function localizedServiceLabel(service: string, lang: Language): string {
  return configurationLabels(lang).services[service] ?? translations.en.services[service] ?? serviceDisplayName(service)
}

export function configurationCountLabel(lang: Language, total: number, active: number): string {
  const labels = configurationLabels(lang)
  const locale = translations[lang] ? lang : 'en'
  const plurals = new Intl.PluralRules(locale)
  const numbers = new Intl.NumberFormat(locale)
  const configurations = plurals.select(total) === 'one' ? labels.configuration : labels.configurations
  const activeConfigurations = plurals.select(active) === 'one' ? labels.active : labels.activePlural
  return `${numbers.format(total)} ${configurations} · ${numbers.format(active)} ${activeConfigurations}`
}
