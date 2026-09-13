from dataclasses import dataclass, field


@dataclass
class RawSection:
    breadcrumb: str
    title: str
    text: str
    code_blocks: list[str] = field(default_factory=list)
    level: int = 3
    # "Sayfaları birebir slayt olarak kullan" modunda (şu an sadece PDF), bu
    # bölümün kaynaklandığı sayfanın rasterize edilmiş görüntüsünün yolu.
    # Normal ayrıştırmada None kalır.
    page_image: str | None = None
    # "Diyagram/görsel çıkar" modunda: sayfadan PDF'in kendi içine gömülü
    # GERÇEK bir raster görselin (ör. bir diyagram/grafik) ham baytlarından
    # çıkarılmış dosya yolu — hiçbir şey üretilmiyor/halüsine edilmiyor,
    # kaynaktaki pikseller birebir kopyalanıyor. page_image'ten farkı: bu,
    # tüm sayfa değil, sayfadaki TEK bir gömülü görsel.
    embedded_image: str | None = None


@dataclass
class WordTiming:
    text: str
    start: float
    end: float


@dataclass
class SynthResult:
    duration: float
    words: list[WordTiming] | None = None


@dataclass
class Slide:
    title: str
    bullets: list[str] = field(default_factory=list)
    code: str | None = None
    narration: str = ""
    level: str = "topic"  # "chapter" (divider slide) or "topic"
    # Bu slaydı üreten ham kaynak bölüm(ler)inin generation_checkpoint.source_fingerprint
    # kimlikleri ve (görüntüleme için) başlıkları. Elle eklenen slaytlarda boştur.
    source_section_ids: list[str] = field(default_factory=list)
    source_titles: list[str] = field(default_factory=list)
    manually_edited: bool = False
    # "Sayfaları birebir slayt olarak kullan" modunda: kaynak sayfanın görüntü
    # yolu. Doluysa render_slide bizim temamızı çizmek yerine bu görüntüyü
    # olduğu gibi (letterbox'lanmış) kullanır.
    background_image: str | None = None
    # "Diyagram/görsel çıkar" modunda: kaynak sayfadan çıkarılmış GERÇEK
    # (üretilmemiş) bir görselin yolu. Doluysa render_slide, kod örneği
    # düzenindeki gibi içerik panelini ikiye bölüp maddeleri sola, bu görseli
    # sağa yerleştirir — background_image'in aksine tema tasarımı korunur.
    embedded_image: str | None = None

    def to_dict(self):
        return {
            "title": self.title,
            "bullets": self.bullets,
            "code": self.code,
            "narration": self.narration,
            "level": self.level,
            "sourceSectionIds": self.source_section_ids,
            "sourceTitles": self.source_titles,
            "manuallyEdited": self.manually_edited,
            "backgroundImage": self.background_image,
            "embeddedImage": self.embedded_image,
        }

    @staticmethod
    def from_dict(d: dict) -> "Slide":
        return Slide(
            title=d.get("title", ""),
            bullets=d.get("bullets", []) or [],
            code=d.get("code"),
            narration=d.get("narration", ""),
            level=d.get("level", "topic"),
            source_section_ids=list(d.get("sourceSectionIds", []) or []),
            source_titles=list(d.get("sourceTitles", []) or []),
            manually_edited=bool(d.get("manuallyEdited", False)),
            background_image=d.get("backgroundImage"),
            embedded_image=d.get("embeddedImage"),
        )
