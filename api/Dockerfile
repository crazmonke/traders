# 운영 배포용 php-fpm 이미지. nginx는 deploy/nginx/ai-trading.conf 참고.
FROM php:8.3-fpm

RUN docker-php-ext-install pdo_mysql opcache \
    && docker-php-ext-configure opcache --enable-opcache

COPY --from=composer:2 /usr/bin/composer /usr/bin/composer

WORKDIR /var/www/html
COPY . .
RUN composer install --no-interaction --no-dev --optimize-autoloader

EXPOSE 9000
CMD ["php-fpm"]
